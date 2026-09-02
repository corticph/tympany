"""Per-user analysis history, persisted as JSON files on disk.

Layout:
    <DATA_DIR>/history/<user_key>/<analysis_id>.json

DATA_DIR is the env var TYMPANY_DATA_DIR, defaulting to a per-user OS data
directory (see tympany.paths.data_dir).

Each file is one analysis. The edits the user makes on the results page
(reclassification, description, exclusion) are persisted here so that reopening
a prior review restores it exactly as the user left it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tympany.paths import data_dir

_CLASSIFICATIONS = [
    "misrecognition",
    "context_dependent",
    "formatting_error",
    "replacement_candidate",
]
_RISK_LEVELS = ["high", "medium", "low"]


def _data_dir() -> Path:
    return data_dir()


def _history_root() -> Path:
    return _data_dir() / "history"


def user_key(email: str) -> str:
    """Stable, filesystem-safe directory name for a user email."""
    email = (email or "local").strip().lower()
    if email == "local":
        return "local"
    slug = re.sub(r"[^a-z0-9]+", "_", email).strip("_") or "user"
    short = hashlib.sha256(email.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{short}"


def _user_dir(email: str) -> Path:
    return _history_root() / user_key(email)


def _new_id() -> str:
    """Timestamp-prefixed id so files sort chronologically by name."""
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Edit normalization & counts
# ---------------------------------------------------------------------------

def normalize_edits(rows: list[dict]) -> list[dict]:
    """Turn raw CSV rows into edit records with description/excluded defaults.

    Mirrors the client-side defaults in results.html so a freshly-saved
    analysis renders identically to today's first render:
      - description defaults to category with underscores spaced out
      - excluded defaults to risk_level == "low" (plus compound_split/merge)
    """
    out: list[dict] = []
    for row in rows:
        category = row.get("category", "") or ""
        risk = row.get("risk_level", "") or ""
        description = row.get("description")
        if description is None or description == "":
            description = category.replace("_", " ")
        excluded = row.get("excluded")
        if isinstance(excluded, str):
            excluded = excluded.lower() == "true"
        elif excluded is None:
            # Auto-exclude low-risk items, plus compound boundary artifacts
            # (tokenization split/merge) which are not edits worth surfacing.
            excluded = risk == "low" or category in ("compound_split", "compound_merge")
        flagged = row.get("flagged")
        if isinstance(flagged, str):
            flagged = flagged.lower() == "true"
        out.append({
            "file": row.get("file", ""),
            "example": row.get("example", ""),
            "speaker": row.get("speaker", ""),
            "ref": row.get("ref", ""),
            "gen": row.get("gen", ""),
            "op": row.get("op", ""),
            "category": category,
            "classification": row.get("classification", ""),
            "risk_level": risk,
            "replacement_candidate": row.get("replacement_candidate", ""),
            "detail": row.get("detail", ""),
            "description": description,
            "excluded": bool(excluded),
            "flagged": bool(flagged),
        })
    return out


def compute_counts(edits: list[dict]) -> dict:
    by_risk = {r: 0 for r in _RISK_LEVELS}
    by_classification = {c: 0 for c in _CLASSIFICATIONS}
    excluded = 0
    for e in edits:
        if e.get("risk_level") in by_risk:
            by_risk[e["risk_level"]] += 1
        if e.get("classification") in by_classification:
            by_classification[e["classification"]] += 1
        if e.get("excluded"):
            excluded += 1
    return {
        "total": len(edits),
        "excluded": excluded,
        "by_risk": by_risk,
        "by_classification": by_classification,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save_analysis(
    email: str,
    filename: str,
    llm_used: bool,
    edits: list[dict],
    samples: Optional[list[dict]] = None,
    original_metrics: Optional[dict] = None,
    source: Optional[dict] = None,
) -> str:
    edits = normalize_edits(edits)
    analysis_id = _new_id()
    now = _now_iso()
    record = {
        "id": analysis_id,
        "user": (email or "local"),
        "filename": filename,
        "created_at": now,
        "updated_at": now,
        "llm_used": bool(llm_used),
        "edits": edits,
        "counts": compute_counts(edits),
        # Re-run support. `samples` holds the full per-example token streams
        # needed to reconstruct corrected text; `original_metrics` are the
        # WER/CER from the initial evaluation. `rerun` is filled in when the
        # user re-runs (see set_bewer_rerun).
        "samples": samples or [],
        "original_metrics": original_metrics or {},
        "rerun": None,
        # Provenance. For reports authored in Tympany (run through bewer here)
        # this is {"kind": "authored", ...}; uploads leave it empty. When
        # "authored", the input BeWER report and the (ref, gen) CSV that
        # produced it are stored as sidecar files (see save_source_artifacts).
        "source": source or {},
    }
    user_dir = _user_dir(email)
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / f"{analysis_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return analysis_id


def _path_for(email: str, analysis_id: str) -> Optional[Path]:
    # Guard against path traversal: ids are timestamp-hex only.
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", analysis_id or ""):
        return None
    path = _user_dir(email) / f"{analysis_id}.json"
    return path if path.is_file() else None


def load_analysis(email: str, analysis_id: str) -> Optional[dict]:
    path = _path_for(email, analysis_id)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def update_edits(email: str, analysis_id: str, edits: list[dict]) -> bool:
    """Merge edit state into an existing record, recompute counts. Last write wins."""
    path = _path_for(email, analysis_id)
    if path is None:
        return False
    record = load_analysis(email, analysis_id)
    if record is None:
        return False
    record["edits"] = normalize_edits(edits)
    record["counts"] = compute_counts(record["edits"])
    record["updated_at"] = _now_iso()
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def rename_analysis(email: str, analysis_id: str, new_name: str) -> Optional[str]:
    """Update an analysis's display title (its `filename`). File ids are stable,
    so only the stored name changes. Returns the new name, or None if missing."""
    path = _path_for(email, analysis_id)
    if path is None:
        return None
    record = load_analysis(email, analysis_id)
    if record is None:
        return None
    record["filename"] = new_name
    record["updated_at"] = _now_iso()
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_name


def delete_analysis(email: str, analysis_id: str) -> bool:
    path = _path_for(email, analysis_id)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    # Best-effort cleanup of sidecar files: the re-run report and, for authored
    # reports, the input BeWER report JSON, its source CSV, and terms.
    for sidecar in (
        rerun_report_path(email, analysis_id),
        report_path(email, analysis_id),
        input_csv_path(email, analysis_id),
        analysis_terms_path(email, analysis_id),
    ):
        if sidecar is not None and sidecar.is_file():
            try:
                sidecar.unlink()
            except OSError:
                pass
    return True


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

# Column order matches the client-side exporter in results.html so server and
# browser downloads produce byte-identical CSVs from the same edit state.
CSV_HEADER = [
    "file", "example", "speaker", "ref", "gen", "op",
    "classification", "risk_level", "error_description",
    "excluded", "flagged", "detail",
]


def to_csv(record: dict) -> str:
    """Render a stored analysis's edits as CSV, reflecting persisted edits."""
    buf = io.StringIO()
    # lineterminator="\n" mirrors the browser exporter (default csv uses \r\n).
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for e in record.get("edits", []):
        writer.writerow([
            e.get("file", ""),
            e.get("example", ""),
            e.get("speaker", ""),
            e.get("ref", ""),
            e.get("gen", ""),
            e.get("op", ""),
            e.get("classification", ""),
            e.get("risk_level", ""),
            e.get("description", ""),
            "true" if e.get("excluded") else "false",
            "true" if e.get("flagged") else "false",
            e.get("detail", ""),
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Flagged-rows export (a focused CSV with surrounding context)
# ---------------------------------------------------------------------------

# Like CSV_HEADER but adds the full example reference / generated text so a
# reviewer has the context surrounding each flagged error, not just the diff.
FLAGS_CSV_HEADER = [
    "file", "example", "speaker", "ref", "gen", "op",
    "classification", "risk_level", "error_description", "detail",
    "ref_context", "gen_context",
]


def _example_context(record: dict) -> dict[str, tuple[str, str]]:
    """Map each example number → (full reference text, full generated text).

    Reconstructed from the persisted per-example token streams. Empty for
    analyses saved before the `samples` payload existed.
    """
    context: dict[str, tuple[str, str]] = {}
    for sample in record.get("samples", []) or []:
        ref = " ".join(t.get("text", "") for t in sample.get("ref_tokens", []))
        gen = " ".join(t.get("text", "") for t in sample.get("pred_tokens", []))
        context[str(sample.get("example"))] = (ref, gen)
    return context


def flags_to_csv(record: dict) -> str:
    """CSV of only the flagged edits, with full example ref/gen as context."""
    context = _example_context(record)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FLAGS_CSV_HEADER)
    for e in record.get("edits", []):
        if not e.get("flagged"):
            continue
        ref_ctx, gen_ctx = context.get(str(e.get("example", "")), ("", ""))
        writer.writerow([
            e.get("file", ""),
            e.get("example", ""),
            e.get("speaker", ""),
            e.get("ref", ""),
            e.get("gen", ""),
            e.get("op", ""),
            e.get("classification", ""),
            e.get("risk_level", ""),
            e.get("description", ""),
            e.get("detail", ""),
            ref_ctx,
            gen_ctx,
        ])
    return buf.getvalue()


def flags_download_filename(record: dict) -> str:
    """Suggested filename for the flagged-rows export."""
    date = datetime.now(timezone.utc).date().isoformat()
    return f"tympany_{date}_{download_base(record)}_flags.csv"


def download_base(record: dict) -> str:
    """The report name with the .html extension stripped (legacy uploads)."""
    name = record.get("filename", "") or "report"
    return re.sub(r"\.html$", "", name, flags=re.IGNORECASE) or "report"


def download_filename(record: dict) -> str:
    """Suggested CSV filename: tympany_<download date>_<report name>.csv.

    The date is today (UTC) at the moment of download, not the analysis date.
    """
    date = datetime.now(timezone.utc).date().isoformat()
    return f"tympany_{date}_{download_base(record)}.csv"


# ---------------------------------------------------------------------------
# Re-run results (re-evaluated BeWER report)
# ---------------------------------------------------------------------------

def rerun_report_path(email: str, analysis_id: str) -> Optional[Path]:
    """On-disk path for an analysis's re-run BeWER report JSON (None if invalid)."""
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", analysis_id or ""):
        return None
    return _user_dir(email) / f"{analysis_id}.rerun.bewer.json"


def rerun_report_filename(record: dict) -> str:
    """Suggested download name for the re-run report JSON."""
    return f"bewer_{download_base(record)}.rerun.json"


# ---------------------------------------------------------------------------
# Authored-report artifacts — the BeWER report JSON and its source CSV
# ---------------------------------------------------------------------------

def is_authored(record: dict) -> bool:
    """True when this analysis was generated from text inside Tympany."""
    return (record.get("source") or {}).get("kind") == "authored"


def report_path(email: str, analysis_id: str) -> Optional[Path]:
    """On-disk path for an authored analysis's input BeWER report JSON."""
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", analysis_id or ""):
        return None
    return _user_dir(email) / f"{analysis_id}.bewer.json"


def input_csv_path(email: str, analysis_id: str) -> Optional[Path]:
    """On-disk path for the (ref, gen) CSV that generated an authored report."""
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", analysis_id or ""):
        return None
    return _user_dir(email) / f"{analysis_id}.input.csv"


def analysis_terms_path(email: str, analysis_id: str) -> Optional[Path]:
    """On-disk path for the medical-terms file used to generate an authored report."""
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", analysis_id or ""):
        return None
    return _user_dir(email) / f"{analysis_id}.terms.txt"


def save_source_artifacts(
    email: str, analysis_id: str, report_json: str, input_csv: str,
    medical_terms: Optional[str] = None,
) -> None:
    """Persist the input BeWER report JSON, source CSV, and (optionally) the
    medical-terms file for an analysis. The terms file lets a re-run recompute
    Medical Term Recall against the same vocabulary, keeping it comparable."""
    rpath = report_path(email, analysis_id)
    csv_path = input_csv_path(email, analysis_id)
    if rpath is None or csv_path is None:
        return
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_text(report_json, encoding="utf-8")
    csv_path.write_text(input_csv, encoding="utf-8")
    if medical_terms:
        terms_path = analysis_terms_path(email, analysis_id)
        if terms_path is not None:
            terms_path.write_text(medical_terms, encoding="utf-8")


def report_filename(record: dict) -> str:
    """Suggested download name for the input BeWER report JSON."""
    return f"bewer_{download_base(record)}.json"


def input_csv_filename(record: dict) -> str:
    """Suggested download name for the generating CSV."""
    return f"{download_base(record)}_input.csv"


# ---------------------------------------------------------------------------
# Generated BeWER reports — "generate only" (no analysis)
#
# The create-report page lets a user generate a BeWER report without running a
# full analysis. Each generation is persisted as its own record so the user can
# revisit, view, download, analyze, or delete it from the upload page.
#
# Layout:
#     <DATA_DIR>/generated/<user_key>/<gen_id>.json   (metadata)
#     <DATA_DIR>/generated/<user_key>/<gen_id>.bewer.json   (the BeWER report)
#     <DATA_DIR>/generated/<user_key>/<gen_id>.csv     (the source ref/gen CSV)
# ---------------------------------------------------------------------------

def _generated_dir(email: str) -> Path:
    return _data_dir() / "generated" / user_key(email)


def generated_report_path(email: str, gen_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", gen_id or ""):
        return None
    return _generated_dir(email) / f"{gen_id}.bewer.json"


def generated_csv_path(email: str, gen_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", gen_id or ""):
        return None
    return _generated_dir(email) / f"{gen_id}.csv"


def _generated_meta_path(email: str, gen_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", gen_id or ""):
        return None
    return _generated_dir(email) / f"{gen_id}.json"


def generated_terms_path(email: str, gen_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[0-9]+-[0-9a-f]+", gen_id or ""):
        return None
    return _generated_dir(email) / f"{gen_id}.terms.txt"


def save_generated_report(
    email: str, name: str, report_json: str, csv_text: str, metrics: dict, rows: int,
    medical_terms: Optional[str] = None,
) -> str:
    """Persist a generate-only BeWER report (envelope JSON) and return its id."""
    gen_id = _new_id()
    dir_ = _generated_dir(email)
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{gen_id}.bewer.json").write_text(report_json, encoding="utf-8")
    (dir_ / f"{gen_id}.csv").write_text(csv_text, encoding="utf-8")
    if medical_terms:
        (dir_ / f"{gen_id}.terms.txt").write_text(medical_terms, encoding="utf-8")
    (dir_ / f"{gen_id}.json").write_text(
        json.dumps({
            "id": gen_id,
            "name": name,
            "created_at": _now_iso(),
            "rows": rows,
            "metrics": {
                "wer": metrics.get("wer"),
                "cer": metrics.get("cer"),
                "mtr": metrics.get("mtr"),
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return gen_id


def load_generated(email: str, gen_id: str) -> Optional[dict]:
    path = _generated_meta_path(email, gen_id)
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_generated(email: str) -> list[dict]:
    """Metadata for every generate-only report by this user, newest first."""
    dir_ = _generated_dir(email)
    if not dir_.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(dir_.glob("*.json"), reverse=True):
        if path.name.endswith(".bewer.json"):
            continue  # the report envelope sidecar, not a metadata record
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        metrics = record.get("metrics") or {}
        items.append({
            "id": record.get("id", path.stem),
            "name": record.get("name", ""),
            "created_at": record.get("created_at", ""),
            "rows": record.get("rows", 0),
            "wer": metrics.get("wer"),
            "cer": metrics.get("cer"),
            "mtr": metrics.get("mtr"),
        })
    return items


def rename_generated(email: str, gen_id: str, new_name: str) -> Optional[str]:
    """Update a generated report's display name in its metadata record. The
    file ids (and sidecars) are unchanged. Returns the new name, or None."""
    path = _generated_meta_path(email, gen_id)
    if path is None or not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    record["name"] = new_name
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_name


def delete_generated(email: str, gen_id: str) -> bool:
    """Remove a generated report and its sidecar files."""
    meta_path = _generated_meta_path(email, gen_id)
    if meta_path is None:
        return False
    removed = False
    for path in (
        meta_path,
        generated_report_path(email, gen_id),
        generated_csv_path(email, gen_id),
        generated_terms_path(email, gen_id),
    ):
        if path is not None and path.is_file():
            try:
                path.unlink()
                removed = True
            except OSError:
                pass
    return removed


def generated_report_filename(record: dict) -> str:
    name = record.get("name") or "authored-report"
    return f"bewer_{name}.json"


def generated_csv_filename(record: dict) -> str:
    name = record.get("name") or "authored-report"
    return f"{name}_input.csv"


def set_bewer_rerun(
    email: str,
    analysis_id: str,
    updated_metrics: dict,
    report_json: str,
) -> bool:
    """Persist a re-run's updated metrics and store its BeWER report JSON."""
    path = _path_for(email, analysis_id)
    if path is None:
        return False
    record = load_analysis(email, analysis_id)
    if record is None:
        return False
    rpath = rerun_report_path(email, analysis_id)
    if rpath is None:
        return False
    rpath.write_text(report_json, encoding="utf-8")
    record["rerun"] = {
        "updated_metrics": {
            "wer": updated_metrics.get("wer"),
            "cer": updated_metrics.get("cer"),
            "mtr": updated_metrics.get("mtr"),
        },
        "examples": updated_metrics.get("examples"),
        "run_at": _now_iso(),
    }
    record["updated_at"] = _now_iso()
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _pct(value) -> Optional[float]:
    """Parse a metric display string like '12.50%' into a float, or None."""
    if not value:
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def metrics_view(record: dict) -> dict:
    """Original + updated WER/CER/MTR for templates (values may be None).

    MTR (Medical Term Recall) is present only when a medical-terms file was
    used. Unlike WER/CER, higher MTR is better, so `mtr_improved` is True when
    the updated value rose — the templates colour it accordingly.
    """
    original = record.get("original_metrics") or {}
    # Accept the legacy "canal" key for analyses created before the rename.
    rerun = record.get("rerun") or record.get("canal") or {}
    updated = rerun.get("updated_metrics") or {}

    orig_mtr, upd_mtr = _pct(original.get("mtr")), _pct(updated.get("mtr"))
    mtr_improved = orig_mtr is not None and upd_mtr is not None and upd_mtr > orig_mtr

    return {
        "original_wer": original.get("wer"),
        "original_cer": original.get("cer"),
        "original_mtr": original.get("mtr"),
        "updated_wer": updated.get("wer"),
        "updated_cer": updated.get("cer"),
        "updated_mtr": updated.get("mtr"),
        "mtr_improved": mtr_improved,
        "has_rerun": bool(updated.get("wer") or updated.get("cer")),
    }


def can_rerun(record: dict) -> bool:
    """True when this analysis has the data needed to re-run.

    Analyses saved before the re-run feature lack `samples`, so they must be
    re-uploaded before they can be re-run.
    """
    return bool(record.get("samples"))


def list_history(email: str) -> list[dict]:
    """Metadata for every analysis by this user, newest first (no edit bodies)."""
    user_dir = _user_dir(email)
    if not user_dir.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(user_dir.glob("*.json"), reverse=True):
        if path.name.endswith(".bewer.json"):
            continue  # report/re-run envelope sidecars, not analysis records
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items.append({
            "id": record.get("id", path.stem),
            "filename": record.get("filename", ""),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
            "llm_used": record.get("llm_used", False),
            "counts": record.get("counts", {}),
            "metrics": metrics_view(record),
        })
    return items
