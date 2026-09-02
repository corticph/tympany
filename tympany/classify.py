"""Classify the diff groups of parsed samples into edit rows.

One classification path shared across the app: rule-based categorization
(`tympany.categorize`) with an optional Corti LLM second pass
(`tympany.llm`). An optional local NER pass (`tympany.ner`, ALPHA) can augment
the rule pass with detected medical entities. Samples come from a bewer
envelope via `tympany.parser.from_bewer`.
"""

from __future__ import annotations

from typing import MutableMapping, Optional

from .categorize import categorize_group
from .llm import classify_with_llm, detect_provider
from .ner import detect_entities, ner_enabled


def _sample_medical_terms(sample) -> frozenset[str]:
    """Medical entities detected across a sample's reference + generated text
    (empty unless the optional local NER backend is enabled)."""
    if not ner_enabled():
        return frozenset()
    text = " ".join(t.text for t in sample.ref_tokens)
    text += " " + " ".join(t.text for t in sample.pred_tokens)
    return detect_entities(text)


def _diarization_error_rows(
    sample, ref_segments, gen_segments,
) -> list[dict]:
    """Detect diarization errors for a sample and return edit-row dicts.

    Only runs when both ref and gen segments are available (diarized input).
    The returned rows carry the same shape as word-level edit rows so they
    flow through ``normalize_edits`` and the results table unchanged.
    """
    if not ref_segments or not gen_segments:
        return []

    from .diarize_errors import align_segments
    from .categorize import _CATEGORY_META

    errors = align_segments(ref_segments, gen_segments)
    rows: list[dict] = []
    for err in errors:
        classification, risk = _CATEGORY_META.get(err.category, ("diarization_error", "medium"))
        rows.append({
            "file": sample.file_stem,
            "example": sample.example_num,
            "ref": err.ref_text,
            "gen": err.gen_text,
            "op": "sub",
            "category": err.category,
            "classification": classification,
            "risk_level": risk,
            "replacement_candidate": False,
            "detail": err.detail,
            "speaker": err.speaker,
        })
    return rows


def classify_samples(
    samples,
    llm_provider: Optional[str] = None,
    llm_outcome: Optional[MutableMapping] = None,
    ref_segments: Optional[list] = None,
    gen_segments: Optional[list] = None,
) -> list[dict]:
    """Categorize every diff group across ``samples`` into edit-row dicts.

    Each row matches the shape ``web.history.normalize_edits`` consumes.

    When the LLM pass runs and ``llm_outcome`` is provided, it is populated with
    the pass result (see ``tympany.llm.classify_with_llm``) so callers can tell
    a real LLM classification apart from a silent rule-based fallback.

    When ``ref_segments`` and ``gen_segments`` are provided (diarized input),
    diarization errors (speaker mismatch, merged/split turns, missing/extra
    turns) are detected and appended as additional edit rows.
    """
    provider = detect_provider(llm_provider) if llm_provider else None
    rows: list[dict] = []
    for idx, sample in enumerate(samples):
        medical_terms = _sample_medical_terms(sample)
        edits = [
            edit
            for group in sample.diffs
            for edit in categorize_group(group, medical_terms)
        ]
        if provider:
            edits = classify_with_llm(edits, provider, outcome=llm_outcome)
        for edit in edits:
            rows.append({
                "file": sample.file_stem,
                "example": sample.example_num,
                "ref": " ".join(edit.ref),
                "gen": " ".join(edit.pred),
                "op": edit.op,
                "category": edit.category,
                "classification": edit.classification,
                "risk_level": edit.risk_level,
                "replacement_candidate": edit.is_replacement_candidate,
                "detail": edit.detail,
                "speaker": edit.speaker,
            })

        if ref_segments and gen_segments and idx < len(ref_segments) and idx < len(gen_segments):
            rows.extend(_diarization_error_rows(sample, ref_segments[idx], gen_segments[idx]))

    return rows
