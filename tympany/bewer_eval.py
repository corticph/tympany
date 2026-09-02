"""Evaluate (reference, generated) pairs with the ``bewer`` library directly.

This replaces shelling out to the Corti Canal CLI and scraping its HTML. We call
bewer in-process and serialise a small, stable JSON *envelope* that the rest of
Tympany consumes (see ``tympany.parser.from_bewer``):

    {
      "metrics":  {"wer": "12.50%", "cer": "3.64%", "mtr": "50.00%"|None},
      "settings": {"normalization": true, "medical_terms": true, "key_terms": [...]},
      "examples": [
        {"example": 1, "ref": "...", "hyp": "...", "ops": [<bewer Op.to_dict()>, ...],
         # token-index [start, stop) slices of located key terms (when terms given)
         "ref_key_terms": [[2, 4], ...], "hyp_key_terms": [[2, 4], ...]}
      ]
    }

The ``ops`` are bewer alignment operations (MATCH / SUBSTITUTE / DELETE /
INSERT), which map cleanly onto Tympany's DiffGroup model — far more reliable
than parsing Canal's colour-coded HTML.

bewer is an early-stage (alpha) dependency; pin it in pyproject and treat its API
as liable to change.
"""

from __future__ import annotations

from typing import Optional, Sequence


class BewerError(RuntimeError):
    """Raised when bewer evaluation fails."""


def _pct(value: Optional[float]) -> Optional[str]:
    """Format a 0–1 metric value as a percentage string, e.g. 0.25 -> '25.00%'."""
    if value is None:
        return None
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return None


def run_bewer(
    rows: Sequence[tuple[str, str]],
    *,
    normalization: bool = True,
    medical_terms: Optional[Sequence[str]] = None,
    ref_word_speakers: Optional[list[list[str]]] = None,
    gen_word_speakers: Optional[list[list[str]]] = None,
) -> dict:
    """Evaluate (ref, gen) ``rows`` with bewer and return the JSON envelope.

    ``medical_terms`` enables the key-term-found (MTR) metric. ``normalization``
    is recorded in settings; bewer applies its default standardisation pipeline.
    ``ref_word_speakers`` and ``gen_word_speakers`` (one list per example, one
    speaker label per word) are embedded in the envelope so downstream
    consumers (report renderer, parser) can tag tokens with their speaker.
    """
    try:
        from bewer import Dataset
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise BewerError("The 'bewer' package is not installed.") from exc

    ds = Dataset()
    for ref, gen in rows:
        ds.add(ref=ref, hyp=gen)
    if medical_terms:
        ds.add_key_term_list("medical", list(medical_terms))

    metric_kwargs = {"normalized": bool(normalization)}

    try:
        align = ds.metrics.get("error_align")(**metric_kwargs)
        wer = ds.metrics.get("wer")(**metric_kwargs).value
        cer = ds.metrics.get("cer")(**metric_kwargs).value
    except Exception as exc:
        raise BewerError(f"bewer evaluation failed: {exc}") from exc

    # Medical Term Recall ≈ bewer's key-term-found metric over the registered list.
    mtr: Optional[float] = None
    if medical_terms:
        try:
            mtr = ds.metrics.get("ktf")(vocab="medical", normalized=bool(normalization)).value
        except Exception:
            mtr = None  # alpha API; degrade gracefully rather than fail the run

    examples = []
    for i, (ref, gen) in enumerate(rows):
        ex = ds.examples[i]
        ops = [op.to_dict() for op in align.get_example_metric(ex).alignment]
        entry = {"example": i + 1, "ref": ref, "hyp": gen, "ops": ops}
        if ref_word_speakers and i < len(ref_word_speakers):
            entry["ref_speakers"] = ref_word_speakers[i]
        if gen_word_speakers and i < len(gen_word_speakers):
            entry["hyp_speakers"] = gen_word_speakers[i]
        # Record where bewer located each key term (as token-index [start, stop)
        # slices, aligned 1:1 with the ops' tokens) so the report can box exactly
        # the terms MTR counts — no second, divergent matcher. Best-effort: bewer
        # is alpha, so degrade to no highlighting rather than failing the run.
        if medical_terms:
            try:
                entry["ref_key_terms"] = [
                    [s.start, s.stop]
                    for s in ex.ref.get_key_term_matches(
                        "medical", normalized=bool(normalization)
                    )
                ]
                entry["hyp_key_terms"] = [
                    [s.start, s.stop]
                    for s in ex.hyp.get_key_term_matches(
                        "medical", normalized=bool(normalization)
                    )
                ]
            except Exception:
                pass
        examples.append(entry)

    return {
        "metrics": {"wer": _pct(wer), "cer": _pct(cer), "mtr": _pct(mtr)},
        "settings": {
            "normalization": bool(normalization),
            "medical_terms": bool(medical_terms),
            # The actual term list, so reports can highlight matched terms inline.
            "key_terms": list(medical_terms) if medical_terms else [],
        },
        "examples": examples,
    }


# ---------------------------------------------------------------------------
# Re-run reconstruction (alignment-engine independent)
#
# To measure the impact of excluded errors we rebuild, per example, the full
# reference text and a *corrected* generated text where every excluded diff is
# rewritten back to the reference. Those (ref, corrected_gen) rows are then
# re-evaluated with bewer. This works off the stored per-example token streams
# and edits, so it is independent of how the original report was produced.
# ---------------------------------------------------------------------------

def _split(text: str) -> list[str]:
    return text.split() if text else []


def reconstruct_example(
    ref_tokens: list[dict], pred_tokens: list[dict], edits: list[dict]
) -> tuple[str, str]:
    """Rebuild (reference_text, corrected_generated_text) for one example.

    Matched tokens pass through; for each contiguous diff group, excluded edits
    are rewritten to the reference and kept edits keep the generated tokens.
    """
    ref_text = " ".join(t["text"] for t in ref_tokens)
    out: list[str] = []
    i = j = 0
    nr, npred = len(ref_tokens), len(pred_tokens)
    eidx = 0

    while i < nr or j < npred:
        if (
            i < nr and j < npred
            and ref_tokens[i]["cls"] == "ok"
            and pred_tokens[j]["cls"] == "ok"
        ):
            out.append(pred_tokens[j]["text"])
            i += 1
            j += 1
            continue

        ref_chunk: list[str] = []
        while i < nr and ref_tokens[i]["cls"] != "ok":
            ref_chunk.append(ref_tokens[i]["text"])
            i += 1
        pred_chunk: list[str] = []
        while j < npred and pred_tokens[j]["cls"] != "ok":
            pred_chunk.append(pred_tokens[j]["text"])
            j += 1

        used_ref = used_pred = 0
        group_edits: list[dict] = []
        while eidx < len(edits) and (
            used_ref < len(ref_chunk) or used_pred < len(pred_chunk)
        ):
            edit = edits[eidx]
            eidx += 1
            group_edits.append(edit)
            used_ref += len(_split(edit.get("ref", "")))
            used_pred += len(_split(edit.get("gen", "")))

        if group_edits:
            for edit in group_edits:
                if edit.get("excluded"):
                    out.extend(_split(edit.get("ref", "")))
                else:
                    out.extend(_split(edit.get("gen", "")))
        else:
            out.extend(pred_chunk)

    return ref_text, " ".join(out)


def build_rows(samples: list[dict], edits: list[dict]) -> list[tuple[str, str]]:
    """Reconstruct (ref, corrected_gen) for every example in the analysis."""
    edits_by_example: dict[str, list[dict]] = {}
    for edit in edits:
        edits_by_example.setdefault(str(edit.get("example", "")), []).append(edit)

    rows: list[tuple[str, str]] = []
    for sample in samples:
        example = str(sample.get("example", ""))
        ref, gen = reconstruct_example(
            sample.get("ref_tokens", []),
            sample.get("pred_tokens", []),
            edits_by_example.get(example, []),
        )
        rows.append((ref, gen))
    return rows
