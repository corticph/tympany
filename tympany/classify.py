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


def classify_samples(
    samples,
    llm_provider: Optional[str] = None,
    llm_outcome: Optional[MutableMapping] = None,
) -> list[dict]:
    """Categorize every diff group across ``samples`` into edit-row dicts.

    Each row matches the shape ``web.history.normalize_edits`` consumes.

    When the LLM pass runs and ``llm_outcome`` is provided, it is populated with
    the pass result (see ``tympany.llm.classify_with_llm``) so callers can tell
    a real LLM classification apart from a silent rule-based fallback.
    """
    provider = detect_provider(llm_provider) if llm_provider else None
    rows: list[dict] = []
    for sample in samples:
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
    return rows
