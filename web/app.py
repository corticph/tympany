"""Tympany web application.

Identity: a lightweight email login (no password / external provider) scopes
per-user history. The submitted email is stored in the signed session cookie.

Environment variables:
    SECRET_KEY            — random string for signing session cookies

Corti (required to enable the agent second pass — see tympany/corti.py):
    CORTI_CLIENT_ID
    CORTI_CLIENT_SECRET
    CORTI_TENANT
    CORTI_ENVIRONMENT     — "eu" or "us"

Run:
    uvicorn web.app:app --reload
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from tympany import bewer_eval, corti
from tympany.classify import classify_samples
from tympany.diarize import (
    build_word_speakers,
    flatten_segments,
    is_diarized,
    parse_corti_transcript_json,
)
from tympany.llm import detect_provider
from tympany.parser import from_bewer, metrics_from_bewer, reference_corpus, samples_to_payload
from web import history, report_render, terms

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Tympany")

logger = logging.getLogger("tympany")

# Session signing key. A deployment MUST set SECRET_KEY; when it is unset we fall
# back to a random ephemeral key (never a hardcoded shared secret) so local dev
# still works, but log a loud warning — ephemeral keys don't survive a restart
# and break multi-worker setups.
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "SECRET_KEY is not set — using a random ephemeral key. Logins won't "
        "survive a restart and this is unsafe with multiple workers. Set "
        "SECRET_KEY in the environment for any real deployment."
    )

# Send the session cookie only over HTTPS when TYMPANY_HTTPS is truthy. Leave it
# off for local http:// development; turn it on behind TLS in production.
SECURE_COOKIES = os.environ.get("TYMPANY_HTTPS", "").strip().lower() in ("1", "true", "yes")

# Resource limits (guard against accidental or malicious oversized input).
MAX_UPLOAD_BYTES = int(os.environ.get("TYMPANY_MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_ROWS = int(os.environ.get("TYMPANY_MAX_ROWS", "5000"))
MAX_TERMS = int(os.environ.get("TYMPANY_MAX_TERMS", "20000"))
# Cap the reference text sent to the term-extractor agent (speed/cost guard).
MAX_EXTRACT_CHARS = int(os.environ.get("TYMPANY_MAX_EXTRACT_CHARS", "24000"))

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=SECURE_COOKIES,
    same_site="lax",
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Baseline hardening headers on every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ---------------------------------------------------------------------------
# Auth helpers — lightweight email identity (no password / external provider)
# ---------------------------------------------------------------------------

# Minimal sanity check; this is an internal honor-system identity, not real auth.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _current_user(request: Request) -> Optional[dict]:
    return request.session.get("user")


def _require_login(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    return None


def _user_email(request: Request) -> str:
    user = _current_user(request) or {}
    return user.get("email") or "local"


def _report_html_string(report_json: str, title: str, downloads: Optional[dict] = None) -> str:
    """Render a stored BeWER report (envelope JSON) to an HTML string."""
    try:
        envelope = json.loads(report_json)
    except ValueError:
        envelope = {"metrics": {}, "examples": []}
    view = report_render.canal_view(envelope)
    return templates.env.get_template("bewer_report.html").render(
        report_title=title, downloads=downloads or {}, **view
    )


def _render_report(
    request: Request, report_json: str, title: str, downloads: Optional[dict] = None
) -> HTMLResponse:
    """Serve a stored BeWER report as a viewable HTML page.

    Reports are built from user input, so forbid scripts as defense in depth.
    ``downloads`` carries {"json": url, "html": url} for the report's header.
    """
    resp = HTMLResponse(_report_html_string(report_json, title, downloads))
    resp.headers["Content-Security-Policy"] = "script-src 'none'"
    return resp


def _report_download(report_json: str, title: str, fmt: str, filename_base: str) -> StreamingResponse:
    """Download a report as JSON (the envelope) or HTML (rendered)."""
    if fmt == "html":
        body = _report_html_string(report_json, title)
        return StreamingResponse(
            io.StringIO(body), media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.html"'},
        )
    return StreamingResponse(
        io.StringIO(report_json), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.json"'},
    )


def _create_context(request: Request, error: Optional[str] = None) -> dict:
    """Template context for the Create page (front door, /)."""
    email = _user_email(request)
    return {
        "user": _current_user(request),
        "nav_active": "create",
        "llm_provider": detect_provider(),
        "generated": history.list_generated(email),
        "saved_terms": terms.list_terms(email),
        "error": error,
    }


def _analyze_context(request: Request, error: Optional[str] = None) -> dict:
    """Template context for the Analyze page (/analyze)."""
    email = _user_email(request)
    return {
        "user": _current_user(request),
        "nav_active": "analyze",
        "llm_provider": detect_provider(),
        "history": history.list_history(email),
        "error": error,
    }


def _llm_pass_failed(outcome: dict) -> bool:
    """True when the LLM pass was attempted but produced no real classification —
    the agent was unreachable, or every eligible edit errored and fell back to
    its rule-chain result."""
    if outcome.get("setup_failed"):
        return True
    eligible = outcome.get("eligible", 0)
    return eligible > 0 and outcome.get("failed", 0) >= eligible


def _llm_notice(outcome: dict) -> Optional[str]:
    """A user-facing degradation message when the Corti pass failed wholly or in
    part, else None. Keeps a failed LLM run from looking like a successful one."""
    if not outcome:
        return None
    if _llm_pass_failed(outcome):
        return ("Corti analysis was unavailable — these results use rule-based "
                "classification only.")
    failed = outcome.get("failed", 0)
    eligible = outcome.get("eligible", 0)
    if failed:
        return (f"Corti could not classify {failed} of {eligible} uncertain "
                "edits; those fall back to rule-based classification.")
    return None


def _results_context(
    request: Request,
    record: dict,
    summary: Optional[dict] = None,
    llm_notice: Optional[str] = None,
) -> dict:
    """Template context for results.html, shared by analyze / authored / history."""
    return {
        "user": _current_user(request),
        "nav_active": "analyze",
        "filename": record.get("filename", ""),
        "edits": record.get("edits", []),
        "summary": summary or {},
        "llm_used": record.get("llm_used", False),
        "llm_notice": llm_notice,
        "analysis_id": record.get("id", ""),
        "download_base": history.download_base(record),
        "metrics": history.metrics_view(record),
        "can_rerun": history.can_rerun(record),
        "is_authored": history.is_authored(record),
        "has_speakers": any(e.get("speaker") for e in record.get("edits", [])),
        "has_per_speaker": bool((record.get("original_metrics") or {}).get("per_speaker")),
        "speakers": sorted(set(
            e.get("speaker", "") for e in record.get("edits", [])
            if e.get("speaker")
        )),
    }


# ---------------------------------------------------------------------------
# Routes — auth
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
async def login_submit(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return templates.TemplateResponse(
            request, "login.html", {"error": "invalid_email"}, status_code=400
        )
    request.session["user"] = {"email": email, "name": email}
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/health")
async def health():
    """Liveness probe for hosting (no auth, no data access)."""
    return JSONResponse({"status": "ok", "bewer": True})


# ---------------------------------------------------------------------------
# Routes — main app
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Landing page — entry points plus how-to and WER best practices."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "home.html", {
        "user": _current_user(request),
        "nav_active": "home",
    })


@app.get("/create", response_class=HTMLResponse)
async def create_page(request: Request):
    """Create page — create a report, generated-report history, saved term lists."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    error = "That report could not be found." if request.query_params.get("error") == "not_found" else None
    return templates.TemplateResponse(request, "create.html", _create_context(request, error))


@app.get("/analyze", response_class=HTMLResponse)
async def analyze_page(request: Request):
    """The Analyze page — upload a report, how-it-works, and analysis history."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    error = "That analysis could not be found." if request.query_params.get("error") == "not_found" else None
    return templates.TemplateResponse(request, "analyze.html", _analyze_context(request, error))


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    file: UploadFile = File(...),
    use_llm: str = Form(default="off"),
):
    redirect = _require_login(request)
    if redirect:
        return redirect

    if not file.filename or not file.filename.lower().endswith(".json"):
        return templates.TemplateResponse(
            request, "analyze.html",
            _analyze_context(request, "Please upload a BeWER report (.json)."),
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return templates.TemplateResponse(
            request, "analyze.html",
            _analyze_context(request, f"That file is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."),
            status_code=400,
        )

    try:
        envelope = json.loads(_decode_upload(content))
        samples = from_bewer(envelope, file_stem=Path(file.filename).stem)
    except (ValueError, AttributeError):
        return templates.TemplateResponse(
            request, "analyze.html",
            _analyze_context(request, "That doesn't look like a BeWER report JSON."),
            status_code=400,
        )
    if not samples:
        return templates.TemplateResponse(
            request, "analyze.html",
            _analyze_context(request, "No examples found in that BeWER report."),
            status_code=400,
        )

    llm_provider: Optional[str] = detect_provider() if use_llm == "on" else None
    llm_outcome: dict = {}
    edits = history.normalize_edits(
        await asyncio.to_thread(classify_samples, samples, llm_provider, llm_outcome)
    )
    # "Used" only if the LLM was requested, configured, and actually classified
    # something — a wholesale failure is recorded as rule-based (no LLM badge).
    llm_used = bool(llm_provider) and not _llm_pass_failed(llm_outcome)

    analysis_id = history.save_analysis(
        _user_email(request), file.filename, llm_used, edits,
        samples=samples_to_payload(samples),
        original_metrics=metrics_from_bewer(envelope),
        source={"kind": "uploaded"},
    )
    record = history.load_analysis(_user_email(request), analysis_id)

    return templates.TemplateResponse(
        request, "results.html",
        _results_context(request, record, llm_notice=_llm_notice(llm_outcome)),
    )


# ---------------------------------------------------------------------------
# Routes — create an initial report from raw text (run BeWER, then analyze)
# ---------------------------------------------------------------------------

def _render_create_form(
    request: Request,
    *,
    error: Optional[str] = None,
    form: Optional[dict] = None,
    draft: Optional[dict] = None,
    status: int = 200,
) -> HTMLResponse:
    """Render the create-report page (fresh, with an error, or with a draft)."""
    return templates.TemplateResponse(request, "reports_new.html", {
        "user": _current_user(request),
        "nav_active": "create",
        "llm_provider": detect_provider(),
        "error": error,
        "form": form or {},
        "draft": draft,
        "saved_terms": terms.list_terms(_user_email(request)),
    }, status_code=status)


@app.get("/reports/new", response_class=HTMLResponse)
async def reports_new_form(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    return _render_create_form(request)


@dataclass
class _ReportInputs:
    """Validated, resolved inputs shared by the two create routes."""
    form: dict
    rows: list
    terms_content: Optional[str]
    report_name: str
    normalize_on: bool
    input_csv: str
    ref_word_speakers: Optional[list[list[str]]] = None
    gen_word_speakers: Optional[list[list[str]]] = None
    diarized: bool = False
    ref_segments: Optional[list] = None
    gen_segments: Optional[list] = None
    per_speaker: bool = False


def _form_samples(references: list[str], generateds: list[str]) -> list[dict]:
    """Zip the per-row reference/generated fields into {ref, gen} dicts for
    re-rendering the form (preserving exactly what the user typed). Always at
    least one row so the create form has something to render."""
    refs = list(references or [])
    gens = list(generateds or [])
    width = max(len(refs), len(gens), 1)
    refs += [""] * (width - len(refs))
    gens += [""] * (width - len(gens))
    return [{"ref": r, "gen": g} for r, g in zip(refs, gens)]


async def _prepare_report(
    request: Request, *, input_mode: str, name: str,
    reference: list[str], generated: list[str],
    csv_file: Optional[UploadFile], normalization: str, use_llm: str,
    terms_mode: str, terms_text: str, terms_file: Optional[UploadFile],
    terms_saved: str, terms_save_as: str,
    ref_file: Optional[UploadFile] = None,
    gen_file: Optional[UploadFile] = None,
    per_speaker: str = "off",
):
    """Validate inputs and resolve medical terms for the create routes.

    Returns a _ReportInputs on success, or a rendered create-form response
    (validation error) for the caller to return directly.
    """
    email = _user_email(request)
    form = {
        "input_mode": input_mode, "name": name,
        "samples": _form_samples(reference, generated),
        "normalization": normalization, "use_llm": use_llm,
        "terms_mode": terms_mode, "terms_text": terms_text,
        "terms_saved": terms_saved, "terms_save_as": terms_save_as,
    }
    try:
        if input_mode == "corti":
            rows, ref_ws, gen_ws, diarized, ref_segs, gen_segs = await _read_corti_rows(ref_file, gen_file)
        else:
            rows = await _read_rows(input_mode, reference, generated, csv_file)
            ref_ws = gen_ws = None
            diarized = False
            ref_segs = gen_segs = None
        terms_content = await _resolve_terms(
            email, terms_mode, terms_text, terms_file, terms_saved, terms_save_as
        )
    except ValueError as exc:
        return _render_create_form(request, form=form, error=str(exc), status=400)
    return _ReportInputs(
        form=form,
        rows=rows,
        terms_content=terms_content,
        report_name=_safe_report_name(name),
        normalize_on=normalization == "on",
        input_csv=_rows_to_csv(rows),
        ref_word_speakers=ref_ws,
        gen_word_speakers=gen_ws,
        diarized=diarized,
        ref_segments=[ref_segs] if ref_segs else None,
        gen_segments=[gen_segs] if gen_segs else None,
        per_speaker=per_speaker == "on" and diarized,
    )


async def _run_bewer(prep: _ReportInputs) -> dict:
    """Evaluate prep's rows with bewer (off the event loop), returning the envelope."""
    terms = list(_split_terms(prep.terms_content)) if prep.terms_content else None
    if prep.per_speaker and prep.ref_segments and prep.gen_segments:
        return await asyncio.to_thread(
            bewer_eval.run_bewer_diarized,
            prep.ref_segments[0], prep.gen_segments[0],
            normalization=prep.normalize_on, medical_terms=terms,
        )
    return await asyncio.to_thread(
        bewer_eval.run_bewer, prep.rows,
        normalization=prep.normalize_on, medical_terms=terms,
        ref_word_speakers=prep.ref_word_speakers,
        gen_word_speakers=prep.gen_word_speakers,
    )


@app.post("/reports/generate", response_class=HTMLResponse)
async def reports_generate(
    request: Request,
    input_mode: str = Form(default="paste"),
    name: str = Form(default=""),
    reference: list[str] = Form(default=[]),
    generated: list[str] = Form(default=[]),
    csv_file: Optional[UploadFile] = File(default=None),
    normalization: str = Form(default="off"),
    use_llm: str = Form(default="off"),
    terms_mode: str = Form(default="none"),
    terms_text: str = Form(default=""),
    terms_file: Optional[UploadFile] = File(default=None),
    terms_saved: str = Form(default=""),
    terms_save_as: str = Form(default=""),
    ref_file: Optional[UploadFile] = File(default=None),
    gen_file: Optional[UploadFile] = File(default=None),
    per_speaker: str = Form(default="off"),
):
    """Generate a BeWER report only (no analysis); stay on the page to iterate."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    prep = await _prepare_report(
        request, input_mode=input_mode, name=name, reference=reference,
        generated=generated, csv_file=csv_file, normalization=normalization,
        use_llm=use_llm, terms_mode=terms_mode, terms_text=terms_text,
        terms_file=terms_file, terms_saved=terms_saved, terms_save_as=terms_save_as,
        ref_file=ref_file, gen_file=gen_file, per_speaker=per_speaker,
    )
    if not isinstance(prep, _ReportInputs):
        return prep  # a rendered error form

    try:
        envelope = await _run_bewer(prep)
    except bewer_eval.BewerError as exc:
        return _render_create_form(
            request, form=prep.form, status=502,
            error=f"BeWER could not generate a report: {exc}",
        )

    metrics = envelope["metrics"]
    gen_id = history.save_generated_report(
        _user_email(request), prep.report_name, json.dumps(envelope), prep.input_csv,
        metrics, len(prep.rows), medical_terms=prep.terms_content,
    )
    draft = {
        "id": gen_id,
        "name": prep.report_name,
        "rows": len(prep.rows),
        "wer": metrics.get("wer"),
        "cer": metrics.get("cer"),
        "mtr": metrics.get("mtr"),
    }
    return _render_create_form(request, form=prep.form, draft=draft)


@app.post("/reports/new", response_class=HTMLResponse)
async def reports_new_submit(
    request: Request,
    input_mode: str = Form(default="paste"),
    name: str = Form(default=""),
    reference: list[str] = Form(default=[]),
    generated: list[str] = Form(default=[]),
    csv_file: Optional[UploadFile] = File(default=None),
    normalization: str = Form(default="off"),
    use_llm: str = Form(default="off"),
    terms_mode: str = Form(default="none"),
    terms_text: str = Form(default=""),
    terms_file: Optional[UploadFile] = File(default=None),
    terms_saved: str = Form(default=""),
    terms_save_as: str = Form(default=""),
    ref_file: Optional[UploadFile] = File(default=None),
    gen_file: Optional[UploadFile] = File(default=None),
    per_speaker: str = Form(default="off"),
):
    """Generate a BeWER report and run the full Tympany analysis on it."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    prep = await _prepare_report(
        request, input_mode=input_mode, name=name, reference=reference,
        generated=generated, csv_file=csv_file, normalization=normalization,
        use_llm=use_llm, terms_mode=terms_mode, terms_text=terms_text,
        terms_file=terms_file, terms_saved=terms_saved, terms_save_as=terms_save_as,
        ref_file=ref_file, gen_file=gen_file, per_speaker=per_speaker,
    )
    if not isinstance(prep, _ReportInputs):
        return prep  # a rendered error form

    email = _user_email(request)
    filename = f"{prep.report_name}.bewer.json"
    llm_provider: Optional[str] = detect_provider() if use_llm == "on" else None

    try:
        envelope = await _run_bewer(prep)
    except bewer_eval.BewerError as exc:
        return _render_create_form(
            request, form=prep.form, status=502,
            error=f"BeWER could not generate a report: {exc}",
        )

    samples = from_bewer(
        envelope, file_stem=prep.report_name,
        ref_word_speakers=prep.ref_word_speakers,
        gen_word_speakers=prep.gen_word_speakers,
    )
    llm_outcome: dict = {}
    edits = history.normalize_edits(
        await asyncio.to_thread(
            classify_samples, samples, llm_provider, llm_outcome,
            ref_segments=prep.ref_segments, gen_segments=prep.gen_segments,
        )
    )
    llm_used = bool(llm_provider) and not _llm_pass_failed(llm_outcome)

    # Also persist the generated BeWER report itself, so "Generate & analyze"
    # lists it under Generated BeWER reports just like "Generate report" does.
    history.save_generated_report(
        email, prep.report_name, json.dumps(envelope), prep.input_csv,
        envelope["metrics"], len(prep.rows), medical_terms=prep.terms_content,
    )

    source = {
        "kind": "authored",
        "input": "corti" if input_mode == "corti" else ("csv" if input_mode == "csv" else "paste"),
        "rows": len(prep.rows),
        "settings": {"normalization": prep.normalize_on},
        "medical_terms": bool(prep.terms_content),
        "diarized": prep.diarized,
    }
    analysis_id = history.save_analysis(
        email, filename, llm_used, edits,
        samples=samples_to_payload(samples),
        original_metrics=metrics_from_bewer(envelope), source=source,
    )
    # Persist the input BeWER report JSON, the CSV that generated it, and the
    # medical-terms file (so a re-run can recompute MTR comparably).
    history.save_source_artifacts(
        email, analysis_id, json.dumps(envelope), prep.input_csv,
        medical_terms=prep.terms_content,
    )
    record = history.load_analysis(email, analysis_id)

    return templates.TemplateResponse(
        request, "results.html",
        _results_context(request, record, llm_notice=_llm_notice(llm_outcome)),
    )


# ---------------------------------------------------------------------------
# Routes — generated BeWER reports (generate-only, no analysis)
# ---------------------------------------------------------------------------

@app.get("/reports/generated/{gen_id}/report", response_class=HTMLResponse)
async def generated_report_view(request: Request, gen_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    path = history.generated_report_path(email, gen_id)
    record = history.load_generated(email, gen_id)
    if path is None or not path.is_file():
        return RedirectResponse("/create?error=not_found", status_code=302)
    title = f"{(record or {}).get('name', 'report')} — BeWER report"
    base = f"/reports/generated/{gen_id}/report/download"
    return _render_report(request, path.read_text(encoding="utf-8"), title,
                          downloads={"json": f"{base}?format=json", "html": f"{base}?format=html"})


@app.get("/reports/generated/{gen_id}/report/download")
async def generated_report_download(request: Request, gen_id: str, format: str = "json"):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    record = history.load_generated(email, gen_id)
    path = history.generated_report_path(email, gen_id)
    if record is None or path is None or not path.is_file():
        return RedirectResponse("/create?error=not_found", status_code=302)
    name = record.get("name") or "report"
    return _report_download(path.read_text(encoding="utf-8"), f"{name} — BeWER report",
                            format, f"bewer_{name}")


@app.get("/reports/generated/{gen_id}/csv/download")
async def generated_csv_download(request: Request, gen_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    record = history.load_generated(email, gen_id)
    path = history.generated_csv_path(email, gen_id)
    if record is None or path is None or not path.is_file():
        return RedirectResponse("/create?error=not_found", status_code=302)
    return StreamingResponse(
        io.StringIO(path.read_text(encoding="utf-8")),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="{history.generated_csv_filename(record)}"'
        },
    )


@app.post("/reports/generated/{gen_id}/delete")
async def generated_delete(request: Request, gen_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    history.delete_generated(_user_email(request), gen_id)
    return RedirectResponse("/create", status_code=302)


@app.post("/reports/generated/{gen_id}/rename")
async def generated_rename(request: Request, gen_id: str, name: str = Form(default="")):
    """Rename a generated BeWER report (display name only; ids are stable)."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    history.rename_generated(_user_email(request), gen_id, _safe_report_name(name))
    return RedirectResponse("/create", status_code=302)


@app.post("/reports/generated/{gen_id}/analyze", response_class=HTMLResponse)
async def generated_analyze(request: Request, gen_id: str, use_llm: str = Form(default="off")):
    """Run the full Tympany analysis on a previously generated BeWER report."""
    redirect = _require_login(request)
    if redirect:
        return redirect

    email = _user_email(request)
    record = history.load_generated(email, gen_id)
    report_path = history.generated_report_path(email, gen_id)
    csv_path = history.generated_csv_path(email, gen_id)
    if record is None or report_path is None or not report_path.is_file():
        return RedirectResponse("/create?error=not_found", status_code=302)

    content = report_path.read_text(encoding="utf-8")
    input_csv = csv_path.read_text(encoding="utf-8") if (csv_path and csv_path.is_file()) else ""
    terms_path = history.generated_terms_path(email, gen_id)
    terms_content = (
        terms_path.read_text(encoding="utf-8")
        if (terms_path is not None and terms_path.is_file()) else None
    )
    filename = f"{record.get('name') or 'authored-report'}.bewer.json"
    llm_provider: Optional[str] = detect_provider() if use_llm == "on" else None

    try:
        envelope = json.loads(content)
    except ValueError:
        return RedirectResponse("/create?error=not_found", status_code=302)
    samples = from_bewer(envelope, file_stem=record.get("name") or "report")
    llm_outcome: dict = {}
    edits = history.normalize_edits(
        await asyncio.to_thread(classify_samples, samples, llm_provider, llm_outcome)
    )
    llm_used = bool(llm_provider) and not _llm_pass_failed(llm_outcome)

    source = {
        "kind": "authored", "input": "generated", "rows": record.get("rows", 0),
        "medical_terms": bool(terms_content),
    }
    analysis_id = history.save_analysis(
        email, filename, llm_used, edits,
        samples=samples_to_payload(samples),
        original_metrics=metrics_from_bewer(envelope), source=source,
    )
    history.save_source_artifacts(
        email, analysis_id, content, input_csv, medical_terms=terms_content
    )
    saved = history.load_analysis(email, analysis_id)

    return templates.TemplateResponse(
        request, "results.html",
        _results_context(request, saved, llm_notice=_llm_notice(llm_outcome)),
    )


# ---------------------------------------------------------------------------
# Routes — saved medical-term lists
# ---------------------------------------------------------------------------

@app.post("/terms/delete")
async def terms_delete(request: Request, name: str = Form(...)):
    redirect = _require_login(request)
    if redirect:
        return redirect
    terms.delete_terms(_user_email(request), name)
    return RedirectResponse("/create", status_code=302)


def _render_terms_editor(
    request: Request, *, action: str, heading: str,
    name: str = "", content: str = "", error: Optional[str] = None, status: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(request, "terms_edit.html", {
        "user": _current_user(request),
        "nav_active": "create",
        "action": action,
        "heading": heading,
        "name": name,
        "content": content,
        "count": terms.term_count(content),
        "error": error,
        "corti_configured": corti.is_configured(),
    }, status_code=status)


@app.get("/terms/new", response_class=HTMLResponse)
async def terms_new_form(request: Request):
    redirect = _require_login(request)
    if redirect:
        return redirect
    return _render_terms_editor(request, action="/terms/new", heading="New term list")


@app.post("/terms/new", response_class=HTMLResponse)
async def terms_new_submit(
    request: Request,
    new_name: str = Form(default=""),
    content: str = Form(default=""),
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)

    def _error(message: str) -> HTMLResponse:
        return _render_terms_editor(
            request, action="/terms/new", heading="New term list",
            name=new_name, content=content, error=message, status=400,
        )

    safe_new = terms.safe_name(new_name)
    if not safe_new:
        return _error("Enter a valid name (letters, numbers, dashes).")
    if not terms.normalize_terms(content).strip():
        return _error("A term list needs at least one term.")
    if terms.exists(email, safe_new):
        return _error(f"A term list named “{safe_new}” already exists.")

    terms.save_terms(email, safe_new, content)
    return RedirectResponse("/create", status_code=302)


@app.post("/terms/extract")
async def terms_extract(request: Request, file: UploadFile = File(...)):
    """Extract medical terms from a BeWER report's reference text (JSON response)."""
    redirect = _require_login(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    if not corti.is_configured():
        return JSONResponse(
            {"ok": False, "message": "Corti credentials are not configured, so term "
             "extraction is unavailable."},
            status_code=503,
        )
    if not file.filename or not file.filename.lower().endswith(".json"):
        return JSONResponse(
            {"ok": False, "message": "Upload a BeWER report (.json)."}, status_code=400
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"ok": False, "message": f"That file is too large (limit "
             f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."},
            status_code=400,
        )

    # Read the report and collect the reference (ground-truth) text only.
    try:
        envelope = json.loads(_decode_upload(content))
        ref_text = reference_corpus(from_bewer(envelope))
    except (ValueError, AttributeError):
        return JSONResponse(
            {"ok": False, "message": "That doesn't look like a BeWER report JSON."},
            status_code=400,
        )

    if not ref_text.strip():
        return JSONResponse(
            {"ok": False, "message": "No reference text found in that report."},
            status_code=400,
        )
    ref_text = ref_text[:MAX_EXTRACT_CHARS]

    try:
        extracted = await asyncio.to_thread(corti.extract_terms, ref_text)
    except Exception as exc:
        logger.warning("term extraction failed: %s", exc)
        return JSONResponse(
            {"ok": False, "message": "Term extraction failed. Check the server logs."},
            status_code=502,
        )

    return JSONResponse({"ok": True, "terms": extracted[:MAX_TERMS]})


@app.get("/terms/{name}/edit", response_class=HTMLResponse)
async def terms_edit_form(request: Request, name: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    content = terms.read_terms(_user_email(request), name)
    if content is None:
        return RedirectResponse("/create?error=not_found", status_code=302)
    safe = terms.safe_name(name)
    return _render_terms_editor(
        request, action=f"/terms/{safe}/edit", heading="Edit term list",
        name=safe, content=content,
    )


@app.post("/terms/{name}/edit", response_class=HTMLResponse)
async def terms_edit_submit(
    request: Request,
    name: str,
    new_name: str = Form(default=""),
    content: str = Form(default=""),
):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    safe = terms.safe_name(name)

    if terms.read_terms(email, name) is None:
        return RedirectResponse("/create?error=not_found", status_code=302)

    def _error(message: str) -> HTMLResponse:
        return _render_terms_editor(
            request, action=f"/terms/{safe}/edit", heading="Edit term list",
            name=new_name or safe, content=content, error=message, status=400,
        )

    safe_new = terms.safe_name(new_name)
    if not safe_new:
        return _error("Enter a valid name (letters, numbers, dashes).")
    if not terms.normalize_terms(content).strip():
        return _error("A term list cannot be empty. Use Delete to remove it.")
    if safe_new != safe and terms.exists(email, safe_new):
        return _error(f"A term list named “{safe_new}” already exists.")

    # Save the (possibly renamed) content. Rename = save-new + delete-old.
    terms.save_terms(email, safe_new, content)
    if safe_new != safe:
        terms.delete_terms(email, name)
    return RedirectResponse("/create", status_code=302)


@app.post("/terms/{name}/duplicate")
async def terms_duplicate(request: Request, name: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    terms.duplicate_terms(_user_email(request), name)
    return RedirectResponse("/create", status_code=302)


# ---------------------------------------------------------------------------
# Routes — history
# ---------------------------------------------------------------------------

@app.get("/history/{analysis_id}", response_class=HTMLResponse)
async def history_open(request: Request, analysis_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    record = history.load_analysis(_user_email(request), analysis_id)
    if record is None:
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    return templates.TemplateResponse(
        request, "results.html", _results_context(request, record)
    )


@app.get("/history/{analysis_id}/download")
async def history_download(request: Request, analysis_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    record = history.load_analysis(_user_email(request), analysis_id)
    if record is None:
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    return StreamingResponse(
        io.StringIO(history.to_csv(record)),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{history.download_filename(record)}"'
        },
    )


@app.get("/history/{analysis_id}/flags/download")
async def history_flags_download(request: Request, analysis_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    record = history.load_analysis(_user_email(request), analysis_id)
    if record is None:
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    return StreamingResponse(
        io.StringIO(history.flags_to_csv(record)),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{history.flags_download_filename(record)}"'
        },
    )


@app.post("/history/{analysis_id}/rerun")
async def history_rerun(request: Request, analysis_id: str):
    """Re-run the analysis with excluded errors corrected.

    Reconstructs corrected (ref, gen) pairs, re-evaluates them with bewer using
    the original report's settings, stores the new report + updated WER/CER, and returns
    the updated metrics as JSON.
    """
    redirect = _require_login(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    email = _user_email(request)
    record = history.load_analysis(email, analysis_id)
    if record is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    if not history.can_rerun(record):
        return JSONResponse(
            {"ok": False, "error": "no_samples",
             "message": "This analysis predates the re-run feature. Re-upload "
                        "the report to enable re-run."},
            status_code=409,
        )
    rows = bewer_eval.build_rows(record.get("samples", []), record.get("edits", []))
    settings = record.get("original_metrics") or {}

    # Reuse the same medical terms so the updated MTR stays comparable.
    terms_path = history.analysis_terms_path(email, analysis_id)
    terms = (
        _split_terms(terms_path.read_text(encoding="utf-8"))
        if terms_path is not None and terms_path.is_file() else None
    )

    try:
        envelope = await asyncio.to_thread(
            bewer_eval.run_bewer, rows,
            normalization=settings.get("normalization", True),
            medical_terms=terms,
        )
    except bewer_eval.BewerError as exc:
        return JSONResponse(
            {"ok": False, "error": "bewer_failed", "message": str(exc)},
            status_code=500,
        )

    updated = dict(envelope["metrics"])
    updated["examples"] = len(rows)

    # Per-speaker re-run: reconstruct corrected text per speaker and re-evaluate.
    orig_metrics = record.get("original_metrics") or {}
    if orig_metrics.get("per_speaker"):
        speaker_rows = bewer_eval.build_rows_per_speaker(
            record.get("samples", []), record.get("edits", [])
        )
        per_speaker_metrics: dict[str, dict] = {}
        for spk, spk_rows in speaker_rows.items():
            if not spk_rows:
                continue
            try:
                spk_env = await asyncio.to_thread(
                    bewer_eval.run_bewer, spk_rows,
                    normalization=settings.get("normalization", True),
                    medical_terms=terms,
                )
            except bewer_eval.BewerError:
                continue
            per_speaker_metrics[spk] = {
                "wer": spk_env["metrics"].get("wer"),
                "cer": spk_env["metrics"].get("cer"),
                "mtr": spk_env["metrics"].get("mtr"),
                "ref_words": sum(len(r[0].split()) for r in spk_rows),
                "gen_words": sum(len(r[1].split()) for r in spk_rows),
            }
        if per_speaker_metrics:
            updated["per_speaker"] = per_speaker_metrics

    history.set_bewer_rerun(email, analysis_id, updated, json.dumps(envelope))
    return JSONResponse({"ok": True, "metrics": history.metrics_view(
        history.load_analysis(email, analysis_id)
    )})


@app.get("/history/{analysis_id}/rerun-report/download")
async def history_rerun_report_download(request: Request, analysis_id: str, format: str = "json"):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    record = history.load_analysis(email, analysis_id)
    report_path = history.rerun_report_path(email, analysis_id)
    if record is None or report_path is None or not report_path.is_file():
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    return _report_download(report_path.read_text(encoding="utf-8"), "Re-run BeWER report",
                            format, f"bewer_{history.download_base(record)}.rerun")


@app.get("/history/{analysis_id}/rerun-report/view", response_class=HTMLResponse)
async def history_rerun_report_view(request: Request, analysis_id: str):
    """View the re-run BeWER report inline in the browser."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    report_path = history.rerun_report_path(email, analysis_id)
    if report_path is None or not report_path.is_file():
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    base = f"/history/{analysis_id}/rerun-report/download"
    return _render_report(request, report_path.read_text(encoding="utf-8"), "Re-run BeWER report",
                          downloads={"json": f"{base}?format=json", "html": f"{base}?format=html"})


@app.get("/history/{analysis_id}/initial-report/view", response_class=HTMLResponse)
async def history_initial_report_view(request: Request, analysis_id: str):
    """View the input BeWER report (authored reports) inline in the browser."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    report_path = history.report_path(email, analysis_id)
    if report_path is None or not report_path.is_file():
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    base = f"/history/{analysis_id}/initial-report/download"
    return _render_report(request, report_path.read_text(encoding="utf-8"), "Input BeWER report",
                          downloads={"json": f"{base}?format=json", "html": f"{base}?format=html"})


@app.get("/history/{analysis_id}/initial-report/download")
async def history_initial_report_download(request: Request, analysis_id: str, format: str = "json"):
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    record = history.load_analysis(email, analysis_id)
    report_path = history.report_path(email, analysis_id)
    if record is None or report_path is None or not report_path.is_file():
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    return _report_download(report_path.read_text(encoding="utf-8"), "Input BeWER report",
                            format, f"bewer_{history.download_base(record)}")


@app.get("/history/{analysis_id}/input-csv/download")
async def history_input_csv_download(request: Request, analysis_id: str):
    """Download the (ref, gen) CSV that generated an authored report."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    email = _user_email(request)
    record = history.load_analysis(email, analysis_id)
    if record is None:
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    csv_path = history.input_csv_path(email, analysis_id)
    if csv_path is None or not csv_path.is_file():
        return RedirectResponse("/analyze?error=not_found", status_code=302)
    return StreamingResponse(
        io.StringIO(csv_path.read_text(encoding="utf-8")),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="{history.input_csv_filename(record)}"'
        },
    )


@app.post("/history/{analysis_id}/edits")
async def history_save_edits(request: Request, analysis_id: str):
    redirect = _require_login(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    edits = payload.get("edits") if isinstance(payload, dict) else None
    if not isinstance(edits, list):
        return JSONResponse({"ok": False, "error": "bad_payload"}, status_code=400)
    ok = history.update_edits(_user_email(request), analysis_id, edits)
    if not ok:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/history/{analysis_id}/delete")
async def history_delete(request: Request, analysis_id: str):
    redirect = _require_login(request)
    if redirect:
        return redirect
    history.delete_analysis(_user_email(request), analysis_id)
    return RedirectResponse("/analyze", status_code=302)


@app.post("/history/{analysis_id}/rename")
async def history_rename(
    request: Request, analysis_id: str,
    name: str = Form(default=""), back: str = Form(default="/analyze"),
):
    """Rename an analysis (display title only). `back` returns the user to the
    page they renamed from (the history table or the open results page)."""
    redirect = _require_login(request)
    if redirect:
        return redirect
    history.rename_analysis(_user_email(request), analysis_id, _safe_report_name(name))
    return RedirectResponse(_safe_local_path(back, "/analyze"), status_code=302)


# ---------------------------------------------------------------------------
# Authoring input → (ref, gen) rows
# ---------------------------------------------------------------------------

# Accepted CSV column names (case-insensitive) for the reference / generated
# transcripts, beyond the canonical `ref` / `gen` that bewer itself uses.
_CSV_REF_ALIASES = ("ref", "reference", "ref_text", "reference_text")
_CSV_GEN_ALIASES = (
    "gen", "generated", "generated_text", "gen_text",
    "hyp", "hypothesis", "pred", "prediction",
)


def _pick_col(fieldnames: Optional[list[str]], aliases: tuple[str, ...]) -> Optional[str]:
    lookup = {(f or "").strip().lower(): f for f in (fieldnames or [])}
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    return None


def _decode_upload(content: bytes) -> str:
    """Decode uploaded bytes to text, tolerant of the encodings users actually send.

    Tries UTF-8 first (BOM-aware, the common case), then falls back to Windows-1252
    — the default for CSVs and text exported from Excel/Windows — so characters like
    °, é, and smart quotes survive instead of degrading to  replacement characters.
    cp1252 leaves five bytes undefined, so a final replace-pass guarantees a result.
    """
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _rows_from_csv(content: bytes) -> list[tuple[str, str]]:
    """Parse an uploaded CSV into (ref, gen) rows. Raises ValueError on bad input."""
    import csv as csv_mod
    text = _decode_upload(content)
    reader = csv_mod.DictReader(io.StringIO(text))
    ref_col = _pick_col(reader.fieldnames, _CSV_REF_ALIASES)
    gen_col = _pick_col(reader.fieldnames, _CSV_GEN_ALIASES)
    if not ref_col or not gen_col:
        raise ValueError(
            "CSV must have a reference column and a generated column "
            "(e.g. 'ref' and 'gen')."
        )
    rows: list[tuple[str, str]] = []
    for row in reader:
        ref = (row.get(ref_col) or "").strip()
        gen = (row.get(gen_col) or "").strip()
        if not ref and not gen:
            continue
        rows.append((ref, gen))
    if not rows:
        raise ValueError("No data rows found in the CSV.")
    return rows


def _collapse_ws(text: str) -> str:
    """Flatten a sample's text to a single line: collapse all runs of
    whitespace (incl. line breaks) to single spaces and trim."""
    return " ".join((text or "").split())


def _rows_from_samples(
    references: list[str], generateds: list[str]
) -> list[tuple[str, str]]:
    """Build (ref, gen) rows from per-sample reference/generated fields.

    Each row is one example. Whitespace within a field — including line
    breaks — is collapsed to single spaces, so a multi-line transcript becomes
    one string. Fully-empty rows are skipped; a row with exactly one side blank
    is a validation error (the user likely forgot a field).
    """
    refs = list(references or [])
    gens = list(generateds or [])
    # The two arrays are emitted row-by-row from the form, so they line up by
    # position. Pad the shorter side defensively rather than dropping a field.
    width = max(len(refs), len(gens))
    refs += [""] * (width - len(refs))
    gens += [""] * (width - len(gens))

    rows: list[tuple[str, str]] = []
    for i, (ref, gen) in enumerate(zip(refs, gens), start=1):
        ref, gen = _collapse_ws(ref), _collapse_ws(gen)
        if not ref and not gen:
            continue  # blank row (e.g. a trailing one the user added)
        if not ref or not gen:
            missing = "reference" if not ref else "generated"
            raise ValueError(f"Sample {i} is missing its {missing} text.")
        rows.append((ref, gen))
    if not rows:
        raise ValueError("Enter at least one sample with both reference and generated text.")
    return rows


def _default_report_name() -> str:
    """Fallback report name when none is given: report-<yyyymmdd>-<hhmmss>.

    Uses server-local time as a safety net; the browser normally fills this in
    using the user's own local time before submitting (see reports_new.html).
    """
    return datetime.now().strftime("report-%Y%m%d-%H%M%S")


def _safe_report_name(name: str) -> str:
    """Sanitise a user-supplied report name into a filename stem."""
    stem = re.sub(r"\.(html|json)$", "", (name or "").strip(), flags=re.IGNORECASE)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-_.")
    return stem or _default_report_name()


def _safe_local_path(path: str, default: str) -> str:
    """Return `path` only if it is a same-site absolute path, else `default`.
    Guards the post-rename redirect against open-redirect via the `back` field."""
    if isinstance(path, str) and path.startswith("/") and not path.startswith("//"):
        return path
    return default


def _rows_to_csv(rows: list[tuple[str, str]]) -> str:
    """Serialise (ref, gen) rows to the CSV text used to generate a report."""
    import csv as csv_mod
    buf = io.StringIO()
    writer = csv_mod.writer(buf, lineterminator="\n")
    writer.writerow(["ref", "gen"])
    writer.writerows(rows)
    return buf.getvalue()


async def _read_rows(
    input_mode: str,
    reference: list[str],
    generated: list[str],
    csv_file: Optional[UploadFile],
) -> list[tuple[str, str]]:
    """Build (ref, gen) rows from whichever input mode the form used."""
    if input_mode == "csv":
        if csv_file is None or not csv_file.filename:
            raise ValueError("Choose a CSV file to upload.")
        raw = await csv_file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError(f"CSV is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
        rows = _rows_from_csv(raw)
    else:
        rows = _rows_from_samples(reference, generated)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Too many examples ({len(rows)}); the limit is {MAX_ROWS}.")
    return rows


async def _read_corti_rows(
    ref_file: Optional[UploadFile],
    gen_file: Optional[UploadFile],
) -> tuple[
    list[tuple[str, str]],
    Optional[list[list[str]]],
    Optional[list[list[str]]],
    bool,
    Optional[list],
    Optional[list],
]:
    """Parse Corti diarized transcript JSON uploads into flat (ref, gen) rows.

    Returns ``(rows, ref_word_speakers, gen_word_speakers, diarized,
    ref_segments, gen_segments)``. Each Corti transcript JSON becomes one
    example. The segment texts are flattened into a single string for bewer;
    the per-word speaker labels and raw segments are preserved for
    per-speaker evaluation and token tagging.
    """
    if ref_file is None or not ref_file.filename:
        raise ValueError("Upload a reference transcript JSON file.")
    if gen_file is None or not gen_file.filename:
        raise ValueError("Upload a generated transcript JSON file.")
    if not ref_file.filename.lower().endswith(".json"):
        raise ValueError("Reference file must be a .json Corti transcript.")
    if not gen_file.filename.lower().endswith(".json"):
        raise ValueError("Generated file must be a .json Corti transcript.")

    ref_raw = await ref_file.read()
    gen_raw = await gen_file.read()
    for raw, label in ((ref_raw, "Reference"), (gen_raw, "Generated")):
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{label} file is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    try:
        ref_segments = parse_corti_transcript_json(_decode_upload(ref_raw))
    except ValueError as exc:
        raise ValueError(f"Reference transcript: {exc}")
    try:
        gen_segments = parse_corti_transcript_json(_decode_upload(gen_raw))
    except ValueError as exc:
        raise ValueError(f"Generated transcript: {exc}")

    if not ref_segments:
        raise ValueError("No transcript segments found in the reference file.")
    if not gen_segments:
        raise ValueError("No transcript segments found in the generated file.")

    ref_text = flatten_segments(ref_segments)
    gen_text = flatten_segments(gen_segments)
    if not ref_text.strip():
        raise ValueError("Reference transcript segments contain no text.")
    if not gen_text.strip():
        raise ValueError("Generated transcript segments contain no text.")

    rows: list[tuple[str, str]] = [(ref_text, gen_text)]
    ref_ws = [build_word_speakers(ref_segments)]
    gen_ws = [build_word_speakers(gen_segments)]
    diarized = is_diarized(ref_segments) or is_diarized(gen_segments)

    if len(rows) > MAX_ROWS:
        raise ValueError(f"Too many examples ({len(rows)}); the limit is {MAX_ROWS}.")
    return rows, ref_ws, gen_ws, diarized, ref_segments, gen_segments


async def _resolve_terms(
    email: str,
    terms_mode: str,
    terms_text: str,
    terms_file: Optional[UploadFile],
    terms_saved: str,
    terms_save_as: str,
) -> Optional[str]:
    """Resolve the medical-terms text from paste/upload/saved input (or None).

    When ``terms_save_as`` is given, the resolved list is also persisted as a
    reusable saved list for the user.
    """
    content: Optional[str] = None
    if terms_mode == "paste":
        content = terms.normalize_terms(terms_text)
    elif terms_mode == "upload":
        if terms_file is not None and terms_file.filename:
            raw = await terms_file.read()
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError(f"Terms file is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
            content = terms.normalize_terms(_decode_upload(raw))
    elif terms_mode == "saved":
        content = terms.read_terms(email, terms_saved)

    if content is not None and not content.strip():
        content = None

    if content and terms.term_count(content) > MAX_TERMS:
        raise ValueError(f"Too many terms ({terms.term_count(content)}); the limit is {MAX_TERMS}.")

    if content and terms_save_as.strip():
        terms.save_terms(email, terms_save_as, content)

    return content


def _split_terms(content: Optional[str]) -> list[str]:
    """Split stored medical-terms text into a list of non-empty terms."""
    return [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
