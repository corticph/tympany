"""Tests for medical-term extraction (reference corpus, agent parsing, route)."""

import io

import pytest

from tympany import corti
from tympany.parser import Sample, Token, reference_corpus


# ── reference_corpus (reference tokens only) ────────────────────────────────

def _sample(num, ref_words, pred_words):
    return Sample(
        file_stem="r", example_num=num,
        ref_tokens=tuple(Token("ok", w) for w in ref_words),
        pred_tokens=tuple(Token("ok", w) for w in pred_words),
        diffs=(),
    )


def test_reference_corpus_uses_reference_only():
    samples = [
        _sample(1, ["the", "patient", "has", "hypertension"], ["the", "patient", "has", "hypotension"]),
        _sample(2, ["history", "of", "CABG"], ["history", "of", "bypass"]),
    ]
    corpus = reference_corpus(samples)
    assert corpus == "the patient has hypertension\nhistory of CABG"
    assert "hypotension" not in corpus and "bypass" not in corpus  # never the generated side


def test_reference_corpus_skips_empty_examples():
    assert reference_corpus([_sample(1, [], [])]) == ""


# ── corti.extract_terms parsing (network mocked) ────────────────────────────

def test_extract_terms_dedupes_and_strips(monkeypatch):
    monkeypatch.setattr(corti, "ensure_agent", lambda key="term_extractor": "agent-1")
    monkeypatch.setattr(corti, "send_message",
                        lambda prompt, agent_id: "metformin\n- aspirin\nMetformin\n\n• CABG\n")
    out = corti.extract_terms("some reference text")
    assert out == ["metformin", "aspirin", "CABG"]  # bullets stripped, case-insensitive dedupe


def test_extract_terms_empty_input_no_call(monkeypatch):
    monkeypatch.setattr(corti, "ensure_agent", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    assert corti.extract_terms("   ") == []


# ── /terms/extract route (uploads a BeWER report JSON) ──────────────────────

import json as _json
from pathlib import Path as _Path

_ENVELOPE_BYTES = (_Path(__file__).parent / "fixtures" / "bewer_sample.json").read_bytes()


def _json_upload():
    return {"file": ("report.bewer.json", io.BytesIO(_ENVELOPE_BYTES), "application/json")}


def test_extract_route_gated_off_when_unconfigured(auth, monkeypatch):
    import web.app as app
    monkeypatch.setattr(app.corti, "is_configured", lambda: False)
    r = auth.post("/terms/extract", files=_json_upload())
    assert r.status_code == 503
    assert "not configured" in r.json()["message"]


def test_extract_route_success(auth, monkeypatch):
    import web.app as app
    monkeypatch.setattr(app.corti, "is_configured", lambda: True)
    monkeypatch.setattr(app.corti, "extract_terms", lambda text: ["hypertension", "metformin"])
    r = auth.post("/terms/extract", files=_json_upload())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["terms"] == ["hypertension", "metformin"]


def test_extract_route_rejects_non_json(auth, monkeypatch):
    import web.app as app
    monkeypatch.setattr(app.corti, "is_configured", lambda: True)
    r = auth.post("/terms/extract", files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")})
    assert r.status_code == 400


def test_extract_route_requires_login(client):
    r = client.post("/terms/extract", files=_json_upload())
    assert r.status_code in (302, 401)
