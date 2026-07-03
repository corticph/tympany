"""Tests for the rule-based categorizer, focused on the number-family rules.

These exercise the num2words-backed hardening of `rule_number_format` /
`rule_ordinal_format`, which recognises compound spellings across English,
French, German and Danish (e.g. "soixante quinze" = 75) regardless of how the
spoken side is tokenised.
"""

import pytest

from tympany.categorize import classify, _canon, _digit_matches_words


def cat(ref, pred):
    return classify(tuple(ref), tuple(pred)).category


# --- cardinals -----------------------------------------------------------

@pytest.mark.parametrize("ref,pred", [
    (["4"], ["four"]),                       # en single token
    (["four"], ["4"]),                       # reversed
    (["75"], ["seventy", "five"]),           # en compound, spaced
    (["75"], ["seventy-five"]),              # en compound, single hyphenated token
    (["75"], ["soixante", "quinze"]),        # fr compound
    (["soixante", "quinze"], ["75"]),        # fr compound reversed
    (["2020"], ["zweitausendzwanzig"]),      # de compound, single token
    (["2020"], ["totusinde", "og", "tyve"]), # da compound with connector word
    (["2020"], ["two", "thousand", "and", "twenty"]),  # en connector dropped
])
def test_number_format_compound(ref, pred):
    assert cat(ref, pred) == "number_format"


# --- ordinals ------------------------------------------------------------

@pytest.mark.parametrize("ref,pred", [
    (["3"], ["third"]),                      # en single
    (["21"], ["twenty-first"]),              # en compound
    (["21"], ["vingt", "et", "unième"]),     # fr compound with connector
    (["3"], ["dritte"]),                     # de single
])
def test_ordinal_format_compound(ref, pred):
    assert cat(ref, pred) == "ordinal_format"


# --- guards: must NOT over-claim ----------------------------------------

@pytest.mark.parametrize("ref,pred", [
    (["75"], ["seventy", "five", "percent"]),  # extra word breaks the match
    (["75"], ["eighty", "five"]),              # wrong number
    (["123"], ["gibberish"]),                  # not a number spelling at all
])
def test_number_format_rejects_non_matches(ref, pred):
    assert cat(ref, pred) != "number_format"


def test_canon_drops_connectors_and_separators():
    assert _canon(("two", "thousand", "and", "twenty")) == "twothousandtwenty"
    assert _canon(("seventy-five",)) == "seventyfive"
    assert _canon(("vingt", "et", "un")) == "vingtun"


def test_digit_matches_words_rejects_oversized_input():
    # 13+ digit "numbers" are not spelled out (phone numbers, IDs, etc.)
    assert _digit_matches_words("1234567890123", ["whatever"]) is False


# --- dynamic medical-entity rule (optional local NER) -------------------

def test_medical_entity_set_upgrades_to_high_risk():
    # Two distant, non-medical tokens: the static rules call this a plain
    # misrecognition (nothing in the static drug list, not a close spelling).
    base = classify(("morning",), ("evening",))
    assert base.category == "misrecognition"
    # With one side supplied as a detected entity (as the local NER pass would),
    # it is flagged explicitly as medication_or_device / high risk.
    tagged = classify(("morning",), ("evening",), frozenset({"morning"}))
    assert tagged.category == "medication_or_device"
    assert tagged.risk_level == "high"


def test_medical_entity_set_does_not_override_formatting():
    # A benign number-format diff stays a formatting_error even if a medical
    # term set is present (the number rule runs ahead of the entity rule).
    edit = classify(("4",), ("four",), frozenset({"four"}))
    assert edit.category == "number_format"


def test_ner_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TYMPANY_NER", raising=False)
    from tympany import ner
    ner._reset_for_tests()
    assert ner.ner_enabled() is False
    assert ner.detect_entities("the patient takes lisinopril") == frozenset()
