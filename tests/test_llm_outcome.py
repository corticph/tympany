"""The LLM pass records its outcome so the web layer can tell a real Corti
classification apart from a silent rule-based fallback (degradation notice +
honest `llm_used` flag)."""

import json

import pytest

from tympany import llm
from tympany.categorize import Edit
from web.app import _llm_notice, _llm_pass_failed


def _eligible_edit(ref: str, pred: str) -> Edit:
    # An unknown category maps to ('misrecognition', 'high') — i.e. eligible.
    return Edit(ref=(ref,), pred=(pred,), category="misrecognition")


_GOOD_RESPONSE = json.dumps({
    "category": "spelling_close",
    "classification": "context_dependent",
    "risk_level": "medium",
    "replacement_candidate": False,
    "reasoning": "spelling variant",
})


def _patch_corti(monkeypatch, *, setup_ok=True, classify=None):
    monkeypatch.setattr(llm.corti, "ensure_agent",
                        (lambda: "agent-1") if setup_ok
                        else _raise("agent down"))
    monkeypatch.setattr(llm.corti, "invalidate_agent", lambda: None)
    if classify is not None:
        monkeypatch.setattr(llm.corti, "classify_pair", classify)


def _raise(msg):
    def _f(*_a, **_k):
        raise RuntimeError(msg)
    return _f


def test_outcome_success(monkeypatch):
    _patch_corti(monkeypatch, classify=lambda *_a: _GOOD_RESPONSE)
    edits = [_eligible_edit("a", "b"), _eligible_edit("c", "d")]
    outcome: dict = {}
    llm.classify_with_llm(edits, "corti", outcome=outcome)
    assert outcome == {"eligible": 2, "failed": 0}
    assert not _llm_pass_failed(outcome)
    assert _llm_notice(outcome) is None


def test_outcome_setup_failure(monkeypatch):
    _patch_corti(monkeypatch, setup_ok=False)
    edits = [_eligible_edit("a", "b")]
    outcome: dict = {}
    result = llm.classify_with_llm(edits, "corti", outcome=outcome)
    assert result == edits  # unchanged — everything fell back
    assert outcome["setup_failed"] is True
    assert outcome["eligible"] == 1 and outcome["failed"] == 1
    assert _llm_pass_failed(outcome)
    assert "rule-based" in _llm_notice(outcome)


def test_outcome_all_edits_fail(monkeypatch):
    _patch_corti(monkeypatch, classify=_raise("boom"))
    edits = [_eligible_edit("a", "b"), _eligible_edit("c", "d")]
    outcome: dict = {}
    llm.classify_with_llm(edits, "corti", outcome=outcome)
    assert outcome["eligible"] == 2 and outcome["failed"] == 2
    assert _llm_pass_failed(outcome)


def test_outcome_partial_failure(monkeypatch):
    calls = {"n": 0}

    def _classify(*_a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return _GOOD_RESPONSE

    _patch_corti(monkeypatch, classify=_classify)
    edits = [_eligible_edit("a", "b"), _eligible_edit("c", "d")]
    outcome: dict = {}
    llm.classify_with_llm(edits, "corti", outcome=outcome)
    assert outcome["eligible"] == 2 and outcome["failed"] == 1
    assert not _llm_pass_failed(outcome)  # partial success is not a failed pass
    assert "1 of 2" in _llm_notice(outcome)


def test_no_eligible_edits_leaves_outcome_empty(monkeypatch):
    _patch_corti(monkeypatch, classify=lambda *_a: _GOOD_RESPONSE)
    # A formatting_error edit is not eligible for the LLM pass.
    edits = [Edit(ref=("2",), pred=("two",), category="number_format")]
    outcome: dict = {}
    llm.classify_with_llm(edits, "corti", outcome=outcome)
    assert outcome == {}
    assert _llm_notice(outcome) is None
