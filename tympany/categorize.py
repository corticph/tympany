"""Rule-based categorization of diff groups.

Each diff group is classified into a category, then mapped to a
classification. Risk level is derived from the classification via a
single lookup table — it is not stored independently.

    classification  — one of: formatting_error | replacement_candidate |
                               context_dependent | misrecognition
    risk_level      — derived: low | medium | high (from classification)
    replacement_candidate — derived from category (edge case for
                             spelling_close + abbreviation)

Category → classification mapping
---------------------------------
formatting_error:
    number_format, date_format, year_format

replacement_candidate:
    abbreviation_expansion, roman_numeral, ordinal_format, formatting_marker

context_dependent:
    latin_greek_spelling, spelling_close, compound_split, compound_merge,
    compound_boundary

misrecognition:
    misrecognition, medication_or_device, pure_insertion, pure_deletion
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from rapidfuzz.distance import Levenshtein

from .data import (
    ABBREVIATION_EXPANSIONS,
    ALL_CARDINALS,
    ALL_ORDINALS,
    FORMATTING_MARKERS,
    LATIN_GREEK_PAIRS,
    MEDICATIONS_AND_DEVICES,
    ROMAN_ANCHORS,
    ROMAN_NUMERALS,
    SINGLE_TOKEN_ABBREVS,
)
from .parser import DiffGroup


# ---------------------------------------------------------------------------
# Classification table — single source of truth
# ---------------------------------------------------------------------------

# category → classification
_CATEGORY_TO_CLS: dict[str, str] = {
    "number_format":          "formatting_error",
    "date_format":            "formatting_error",
    "year_format":            "formatting_error",
    "abbreviation_expansion": "replacement_candidate",
    "roman_numeral":          "replacement_candidate",
    "ordinal_format":         "replacement_candidate",
    "formatting_marker":      "replacement_candidate",
    "latin_greek_spelling":   "context_dependent",
    "spelling_close":         "context_dependent",
    "compound_split":         "context_dependent",
    "compound_merge":         "context_dependent",
    "compound_boundary":      "context_dependent",
    "misrecognition":         "misrecognition",
    "medication_or_device":   "misrecognition",
    "pure_insertion":         "misrecognition",
    "pure_deletion":          "misrecognition",
}

# classification → risk_level (derived, never stored independently)
_CLS_RISK: dict[str, str] = {
    "formatting_error":      "low",
    "replacement_candidate": "low",
    "context_dependent":     "medium",
    "misrecognition":        "high",
}


# ---------------------------------------------------------------------------
# Edit dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Edit:
    ref: tuple[str, ...]
    pred: tuple[str, ...]
    category: str
    detail: str = ""

    @property
    def op(self) -> str:
        if not self.ref:
            return "ins"
        if not self.pred:
            return "del"
        return "sub"

    @property
    def classification(self) -> str:
        return _CATEGORY_TO_CLS.get(self.category, "misrecognition")

    @property
    def risk_level(self) -> str:
        return _CLS_RISK.get(self.classification, "high")

    @property
    def is_replacement_candidate(self) -> bool:
        """True when this edit is a consistent mapping that could be codified
        as an STT replacement or command rule."""
        cat = self.category
        if cat in ("abbreviation_expansion", "roman_numeral", "ordinal_format"):
            return True
        if cat in ("number_format", "date_format", "year_format"):
            return True
        if cat == "spelling_close" and (
            any(t in SINGLE_TOKEN_ABBREVS for t in self.ref)
            or any(t in SINGLE_TOKEN_ABBREVS for t in self.pred)
        ):
            return True
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIGIT_RE = re.compile(r"^\d+$")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_ZERO_PADDED_RE = re.compile(r"^0\d$")
_DOSAGE_RE = re.compile(r"^(\d+)x$|^x(\d+)$", re.IGNORECASE)


def _is_digit(t: str) -> bool:
    return bool(_DIGIT_RE.match(t))


def _is_year(t: str) -> bool:
    return bool(_YEAR_RE.match(t))


def _is_number_word(t: str) -> bool:
    return t in ALL_CARDINALS


def _normalized_concat(tokens: tuple[str, ...]) -> str:
    return "".join(tokens).lower()


# Number-word matching via num2words --------------------------------------
# The static cardinal/ordinal sets in data.py only enumerate single tokens;
# they cannot recognise compound spellings (French "soixante quinze" = 75,
# German "zweitausendzwanzig" = 2020). num2words generates the canonical
# spelling for any integer, which we normalise and compare against the spoken
# side of a diff. The static sets remain as a fast path and fallback.

try:
    from num2words import num2words as _num2words
except ImportError:  # pragma: no cover - num2words is a declared dependency
    _num2words = None

_NUMBER_LANGS = ("en", "fr", "de", "da")
_CONNECTORS = frozenset({"and", "et", "og"})  # joining words num2words emits


def _canon(tokens) -> str:
    """Lowercase, split on spaces/hyphens, drop number connectors, concat."""
    out: list[str] = []
    for tok in tokens:
        for word in re.split(r"[\s\-]+", str(tok).lower().strip()):
            if word and word not in _CONNECTORS:
                out.append(word)
    return "".join(out)


def _word_forms(n: int, *, ordinal: bool = False) -> set[str]:
    """Canonical number spellings of n across supported languages."""
    if _num2words is None:
        return set()
    to = "ordinal" if ordinal else "cardinal"
    forms: set[str] = set()
    for lang in _NUMBER_LANGS:
        try:
            forms.add(_canon((_num2words(n, lang=lang, to=to),)))
        except Exception:
            continue
    forms.discard("")
    return forms


def _digit_matches_words(digit: str, words, *, ordinal: bool = False) -> bool:
    """True if the integer `digit` spells out (in any supported language) to
    the same normalised form as the token sequence `words`."""
    if not digit.isdigit() or len(digit) > 12 or not words:
        return False
    target = _canon(tuple(words))
    if not target:
        return False
    return target in _word_forms(int(digit), ordinal=ordinal)


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - (Levenshtein.distance(a, b) / max(len(a), len(b)))


# ---------------------------------------------------------------------------
# Rules — each returns (category, detail) or None; first match wins
# ---------------------------------------------------------------------------

Rule = Callable[[tuple[str, ...], tuple[str, ...]], Optional[tuple[str, str]]]


def rule_compound_boundary(ref, pred):
    """Detect compound split/merge by concatenated string similarity.

    Fires when the ref and pred tokens, concatenated and lowercased, are
    close enough (≥0.85 ratio) that the difference is a compound boundary
    rather than a genuine misrecognition.
    """
    if not ref or not pred or (len(ref) == 1 and len(pred) == 1):
        return None
    joined_ref = _normalized_concat(ref)
    joined_pred = _normalized_concat(pred)
    if _ratio(joined_ref, joined_pred) >= 0.85:
        label = "compound_split" if len(ref) < len(pred) else "compound_merge"
        return label, f"{list(ref)} ↔ {list(pred)}"
    return None


def rule_compound_only_boundary(ref, pred):
    """Detect identical-text substitutions that differ only in compound markers.

    These occur when ErrorAlign reports a SUBSTITUTE op where ref == hyp but
    the hyp carries a compound-boundary marker (left/right partial). The text
    is identical — the only difference is whether the token is part of a
    compound word (e.g., "Früh-" in ref vs "Früh" as left part of "Frühbindestrich"
    in pred). Without this rule they fall through to spelling_close (distance 0,
    similarity 1.00) and get misclassified as context_dependent by the LLM.
    """
    if not ref or not pred:
        return None
    # Only fires for 1:1 substitutions where the text is identical
    if len(ref) == len(pred) and all(r == p for r, p in zip(ref, pred)):
        return "compound_boundary", f"{list(ref)} ↔ {list(pred)}"
    return None


def rule_pure_insertion(ref, pred):
    if not ref and pred:
        if all(t in FORMATTING_MARKERS for t in pred):
            return "formatting_marker", "verbalized formatting inserted"
        return "pure_insertion", f"inserted: {list(pred)}"
    return None


def rule_pure_deletion(ref, pred):
    if ref and not pred:
        return "pure_deletion", f"deleted: {list(ref)}"
    return None


def rule_formatting_marker(ref, pred):
    if pred and all(t in FORMATTING_MARKERS for t in pred):
        if not ref or all(t in FORMATTING_MARKERS for t in ref):
            return "formatting_marker", f"verbalized formatting: {list(pred)}"
    return None


def rule_roman_numeral(ref, pred):
    if any(t in ROMAN_NUMERALS for t in ref) and (
        any(t in ROMAN_ANCHORS for t in pred) or any(_is_number_word(t) for t in pred)
    ):
        return "roman_numeral", f"{list(ref)} ↔ {list(pred)}"
    if any(t in ROMAN_NUMERALS for t in pred) and (
        any(t in ROMAN_ANCHORS for t in ref) or any(_is_number_word(t) for t in ref)
    ):
        return "roman_numeral", f"{list(ref)} ↔ {list(pred)}"
    return None


def rule_ordinal_format(ref, pred):
    for digits, words in ((ref, pred), (pred, ref)):
        if len(digits) == 1 and _is_digit(digits[0]) and words:
            if len(words) == 1 and words[0] in ALL_ORDINALS:
                return "ordinal_format", f"{list(ref)} ↔ {list(pred)}"
            if _digit_matches_words(digits[0], words, ordinal=True):
                return "ordinal_format", f"{list(ref)} ↔ {list(pred)}"
    return None


def rule_date_format(ref, pred):
    # Zero-padded two-digit number (01–09) vs spoken "zero four"
    for short, long in ((ref, pred), (pred, ref)):
        if len(short) == 1 and _ZERO_PADDED_RE.match(short[0]) and "zero" in long:
            return "date_format", f"{list(ref)} ↔ {list(pred)}"
    # Month number ↔ month name
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    if len(ref) == 1 and len(pred) == 1:
        r, p = ref[0], pred[0]
        if months.get(r) == p or months.get(p) == r:
            return "date_format", f"{r} ↔ {p}"
    return None


def rule_year_format(ref, pred):
    if len(ref) == 1 and len(pred) == 1:
        r, p = ref[0], pred[0]
        if _is_year(r) and re.fullmatch(r"\d{2}", p) and r.endswith(p):
            return "year_format", f"{r} ↔ {p}"
        if _is_year(p) and re.fullmatch(r"\d{2}", r) and p.endswith(r):
            return "year_format", f"{r} ↔ {p}"
        if _is_year(r) and _is_number_word(p):
            return "year_format", f"{r} ↔ {p}"
        if _is_year(p) and _is_number_word(r):
            return "year_format", f"{r} ↔ {p}"
    return None


def rule_number_format(ref, pred):
    for digits, words in ((ref, pred), (pred, ref)):
        if len(digits) == 1 and _is_digit(digits[0]) and words:
            if len(words) == 1 and _is_number_word(words[0]):
                return "number_format", f"{list(ref)} ↔ {list(pred)}"
            if _digit_matches_words(digits[0], words):
                return "number_format", f"{list(ref)} ↔ {list(pred)}"
    return None


def rule_abbreviation(ref, pred):
    if tuple(ref) in ABBREVIATION_EXPANSIONS:
        valid_expansions = ABBREVIATION_EXPANSIONS[tuple(ref)]
        if tuple(pred) in valid_expansions:
            return "abbreviation_expansion", f"{list(ref)} ↔ {list(pred)}"
    if tuple(pred) in ABBREVIATION_EXPANSIONS:
        valid_expansions = ABBREVIATION_EXPANSIONS[tuple(pred)]
        if tuple(ref) in valid_expansions:
            return "abbreviation_expansion", f"{list(ref)} ↔ {list(pred)}"
    # Single-token abbreviation on one side
    if len(ref) == 1 and ref[0] in SINGLE_TOKEN_ABBREVS:
        for expansion in ABBREVIATION_EXPANSIONS.get((ref[0],), ()):
            if tuple(pred) == expansion:
                return "abbreviation_expansion", f"{ref[0]} ↔ {list(pred)}"
    if len(pred) == 1 and pred[0] in SINGLE_TOKEN_ABBREVS:
        for expansion in ABBREVIATION_EXPANSIONS.get((pred[0],), ()):
            if tuple(ref) == expansion:
                return "abbreviation_expansion", f"{list(ref)} ↔ {pred[0]}"
    return None


def rule_compound_boundary(ref, pred):
    if not ref or not pred:
        return None
    joined_ref = _normalized_concat(ref)
    joined_pred = _normalized_concat(pred)
    if _ratio(joined_ref, joined_pred) >= 0.85:
        label = "compound_split" if len(ref) < len(pred) else "compound_merge"
        return label, f"{list(ref)} ↔ {list(pred)}"
    return None


def _latin_greek_distance(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 3:
        return False
    for x, y in LATIN_GREEK_PAIRS:
        if a.replace(x, y) == b or b.replace(x, y) == a:
            return True
    return False


def rule_latin_greek_spelling(ref, pred):
    if len(ref) == 1 and len(pred) == 1 and _latin_greek_distance(ref[0], pred[0]):
        return "latin_greek_spelling", f"{ref[0]} ↔ {pred[0]}"
    return None


def rule_medication_or_device(ref, pred):
    if not ref or not pred:
        return None
    flat_ref = " ".join(ref)
    flat_pred = " ".join(pred)
    for med in MEDICATIONS_AND_DEVICES:
        if med in ref:
            return "medication_or_device", f"'{med}' in ref: {flat_ref} ↔ {flat_pred}"
        if med in pred:
            return "medication_or_device", f"'{med}' in gen: {flat_ref} ↔ {flat_pred}"
        for t in ref:
            if len(t) >= 5 and Levenshtein.distance(t, med) <= 2:
                return "medication_or_device", f"'{t}' ≈ '{med}': {flat_ref} ↔ {flat_pred}"
        for t in pred:
            if len(t) >= 5 and Levenshtein.distance(t, med) <= 2:
                return "medication_or_device", f"'{t}' ≈ '{med}': {flat_ref} ↔ {flat_pred}"
    return None


def rule_spelling_close(ref, pred):
    if len(ref) == 1 and len(pred) == 1:
        d = Levenshtein.distance(ref[0], pred[0])
        ratio = _ratio(ref[0], pred[0])
        if d <= 2 or ratio >= 0.8:
            return "spelling_close", f"{ref[0]} ↔ {pred[0]}"
    return None


def rule_other(ref, pred):
    return "misrecognition", f"{list(ref)} ↔ {list(pred)}"


RULES: tuple[Rule, ...] = (
    rule_pure_insertion,
    rule_pure_deletion,
    rule_formatting_marker,
    rule_roman_numeral,
    rule_ordinal_format,
    rule_date_format,
    rule_year_format,
    rule_number_format,
    rule_abbreviation,
    rule_compound_only_boundary,
    rule_compound_boundary,
    rule_medication_or_device,
    rule_latin_greek_spelling,
    rule_spelling_close,
    rule_other,
)


def _edit_stats(ref: tuple[str, ...], pred: tuple[str, ...]) -> str:
    """Return a compact stats string appended to every edit's detail field.

    Format: · N→M tokens · dist N · sim 0.XX
    For pure insertions/deletions the distance equals the length of the
    non-empty side and similarity is 0.
    """
    ref_str  = " ".join(ref)
    pred_str = " ".join(pred)

    tok_part = f"{len(ref)}→{len(pred)} tok"

    if not ref_str and not pred_str:
        return f"  [{tok_part}]"

    if not ref_str or not pred_str:
        non_empty = ref_str or pred_str
        dist      = len(non_empty)
        sim       = 0.0
    else:
        dist = Levenshtein.distance(ref_str, pred_str)
        sim  = _ratio(ref_str, pred_str)

    return f"  [{tok_part} · dist {dist} · sim {sim:.2f}]"


def _make_medical_entity_rule(medical_terms: frozenset[str]) -> Rule:
    """A rule that flags any sub touching a dynamically detected medical entity
    (from the optional local NER pass) as medication_or_device / high risk."""
    def rule(ref, pred):
        if not ref or not pred:
            return None
        if any(t.lower() in medical_terms for t in ref) or any(
            t.lower() in medical_terms for t in pred
        ):
            return "medication_or_device", f"medical entity (NER): {list(ref)} ↔ {list(pred)}"
        return None
    return rule


def _effective_rules(medical_terms: frozenset[str]) -> tuple[Rule, ...]:
    """RULES, plus a NER-driven medical-entity rule inserted just ahead of the
    static medication rule when a dynamic term set is supplied."""
    if not medical_terms:
        return RULES
    idx = RULES.index(rule_medication_or_device)
    return RULES[:idx] + (_make_medical_entity_rule(medical_terms),) + RULES[idx:]


def classify(
    ref: tuple[str, ...],
    pred: tuple[str, ...],
    medical_terms: frozenset[str] = frozenset(),
) -> Edit:
    for rule in _effective_rules(medical_terms):
        result = rule(ref, pred)
        if result is not None:
            category, detail = result
            enriched = (detail + _edit_stats(ref, pred)).strip()
            return Edit(ref=ref, pred=pred, category=category, detail=enriched)
    return Edit(ref=ref, pred=pred, category="misrecognition", detail=_edit_stats(ref, pred).strip())


def _split_group(group: DiffGroup) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Split equal-length substitutions into per-position pairs."""
    if (
        len(group.ref) == len(group.pred)
        and len(group.ref) > 1
        and group.ref and group.pred
    ):
        return [((r,), (p,)) for r, p in zip(group.ref, group.pred)]
    return [(group.ref, group.pred)]


def categorize_group(
    group: DiffGroup, medical_terms: frozenset[str] = frozenset()
) -> list[Edit]:
    """Classify a diff group, preserving multi-token context when possible.

    Try the full group first — multi-token rules (number_format,
    compound_boundary) need the complete token sequence. If the group
    classifies as misrecognition and is an equal-length substitution,
    fall back to per-position splitting so each token gets its own
    classification.
    """
    group_edit = classify(group.ref, group.pred, medical_terms)
    if group_edit.category != "misrecognition":
        return [group_edit]
    if len(group.ref) == len(group.pred) and len(group.ref) > 1 and group.ref and group.pred:
        return [classify((r,), (p,), medical_terms) for r, p in zip(group.ref, group.pred)]
    return [group_edit]
