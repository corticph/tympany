"""Route smoke tests and input-validation tests for the web app."""

import io
import json
import re

import pytest

from tests.conftest import TEST_EMAIL


# ── Health & hardening ──────────────────────────────────────────────────────

def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_home_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_login_rejects_bad_email(client):
    r = client.post("/login", data={"email": "not-an-email"})
    assert r.status_code == 400


# ── Pages & navigation ──────────────────────────────────────────────────────

def test_home_is_landing(auth):
    body = auth.get("/").text
    assert "How it works" in body and "WER best practices" in body
    assert 'href="/create"' in body and 'href="/analyze"' in body  # the two big buttons
    assert 'class="nav-link active">Home' in body


def test_create_page(auth):
    body = auth.get("/create").text
    assert "Create BeWER report" in body
    assert "Saved medical term lists" in body
    assert 'class="nav-link active">Create' in body


def test_analyze_page(auth):
    body = auth.get("/analyze").text
    assert "Analyze BeWER report" in body
    assert "History" in body
    assert 'id="upload-form"' in body
    assert 'class="nav-link active">Analyze' in body


def test_nav_hidden_when_logged_out(client):
    body = client.get("/login").text
    assert 'class="nav-link' not in body  # no nav links without a session


def test_analyze_requires_login(client):
    r = client.get("/analyze", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["location"]


# ── Section-aware redirects ─────────────────────────────────────────────────

def test_history_delete_redirects_to_analyze(auth):
    r = auth.post("/history/1700000000000-deadbeef/delete", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/analyze"


def test_history_open_missing_redirects_to_analyze(auth):
    r = auth.get("/history/1700000000000-deadbeef", follow_redirects=False)
    assert r.status_code == 302 and "/analyze?error=not_found" in r.headers["location"]


def test_term_create_redirects_to_create(auth):
    r = auth.post("/terms/new", data={"new_name": "x", "content": "a"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/create"


def test_generated_missing_redirects_to_create(auth):
    r = auth.get("/reports/generated/1700000000000-deadbeef/report", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/create?error=not_found")


def test_generate_report_from_multiple_samples(auth):
    """The Create page posts one (reference, generated) pair per sample row as
    repeated form fields; a multi-line sample collapses to one example."""
    pytest.importorskip("bewer")
    r = auth.post("/reports/generate", data={
        "input_mode": "paste",
        "name": "multi-sample-test",
        # Repeated fields: one (reference, generated) pair per sample row.
        "reference": ["the patient has\nhypertension today",  # multi-line → 1 example
                      "blood pressure was elevated"],
        "generated": ["the patient has hypotension today",
                      "blood pressure was elevated"],
        "normalization": "on",
        "terms_mode": "none",
    })
    assert r.status_code == 200
    # The post-generate draft card reports two examples.
    assert "from 2 examples" in r.text
    from web import history
    gen = history.list_generated(TEST_EMAIL)
    assert len(gen) == 1 and gen[0]["rows"] == 2


def test_generate_report_unchecked_normalization_uses_raw_bewer_mode(auth):
    """Unchecked checkboxes are omitted from form posts; that must disable
    BeWER normalization instead of falling back to an "on" default."""
    pytest.importorskip("bewer")
    r = auth.post(
        "/reports/generate",
        data={
            "input_mode": "csv",
            "name": "csv-raw-mode",
            "terms_mode": "none",
        },
        files={
            "csv_file": (
                "samples.csv",
                io.BytesIO('ref,gen\n"Hello, Café.",hello cafe\n'.encode("utf-8")),
                "text/csv",
            )
        },
    )
    assert r.status_code == 200

    from web import history

    gen_id = history.list_generated(TEST_EMAIL)[0]["id"]
    report_path = history.generated_report_path(TEST_EMAIL, gen_id)
    envelope = json.loads(report_path.read_text(encoding="utf-8"))

    assert envelope["settings"]["normalization"] is False
    assert envelope["metrics"]["wer"] == "100.00%"
    assert [op["ref"] for op in envelope["examples"][0]["ops"]] == ["Hello", "Café"]


@pytest.mark.parametrize("use_llm", ["off", "on"])
def test_generated_analyze_honors_llm_flag(auth, use_llm):
    """The Analyze dropdown posts use_llm=off|on; both reach the analysis and
    return a results page. (Without Corti creds, 'on' degrades to rule-based.)"""
    import json
    from pathlib import Path
    from web import history
    envelope = (Path(__file__).parent / "fixtures" / "bewer_sample.json").read_text()
    gen_id = history.save_generated_report(
        TEST_EMAIL, "to-analyze", envelope, "ref,gen\na,b\n", {"wer": "1%"}, 2,
    )
    r = auth.post(f"/reports/generated/{gen_id}/analyze", data={"use_llm": use_llm})
    assert r.status_code == 200
    # A fresh analysis was recorded for this report.
    assert any(h["filename"] == "to-analyze.bewer.json" for h in history.list_history(TEST_EMAIL))


def test_rename_generated_report(auth):
    pytest.importorskip("bewer")
    auth.post("/reports/generate", data={
        "input_mode": "paste",
        "name": "old-name",
        "reference": "alpha bravo charlie",
        "generated": "alpha bravo charlie",
        "normalization": "on",
        "terms_mode": "none",
    })
    from web import history
    gen_id = history.list_generated(TEST_EMAIL)[0]["id"]
    r = auth.post(f"/reports/generated/{gen_id}/rename",
                  data={"name": "renamed report"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/create"
    # Sanitised + persisted (display name only; the id is unchanged).
    assert history.load_generated(TEST_EMAIL, gen_id)["name"] == "renamed-report"


def test_rename_analysis(auth):
    from web import history
    analysis_id = history.save_analysis(TEST_EMAIL, "before.json", False, [])
    r = auth.post(f"/history/{analysis_id}/rename",
                  data={"name": "after", "back": f"/history/{analysis_id}"},
                  follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/history/{analysis_id}"
    assert history.load_analysis(TEST_EMAIL, analysis_id)["filename"] == "after"


def test_rename_analysis_rejects_offsite_redirect(auth):
    from web import history
    analysis_id = history.save_analysis(TEST_EMAIL, "x", False, [])
    r = auth.post(f"/history/{analysis_id}/rename",
                  data={"name": "y", "back": "https://evil.example/phish"},
                  follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/analyze"


def test_generate_and_analyze_also_lists_generated_report(auth):
    """'Generate & analyze' records the analysis AND lists the BeWER report
    under Generated BeWER reports (same as 'Generate report')."""
    pytest.importorskip("bewer")
    r = auth.post("/reports/new", data={
        "input_mode": "paste",
        "name": "gen-and-analyze",
        "reference": "the patient has hypertension",
        "generated": "the patient has hypotension",
        "normalization": "on",
        "terms_mode": "none",
        "use_llm": "off",
    })
    assert r.status_code == 200  # lands on results.html
    from web import history
    assert any(h["filename"] == "gen-and-analyze.bewer.json"
               for h in history.list_history(TEST_EMAIL))
    assert any(g["name"] == "gen-and-analyze"
               for g in history.list_generated(TEST_EMAIL))


def test_generate_report_missing_side_is_rejected(auth):
    r = auth.post("/reports/generate", data={
        "input_mode": "paste",
        "reference": "only a reference, no generated",
        "generated": "",
        "terms_mode": "none",
    })
    assert r.status_code == 400 and "missing its generated" in r.text


# ── Saved term lists CRUD via routes ────────────────────────────────────────

def test_terms_new_create(auth):
    from web import terms
    auth.post("/terms/new", data={"new_name": "Cardio Set", "content": "a\nb\nc"})
    assert terms.read_terms(TEST_EMAIL, "Cardio-Set") is not None
    assert terms.term_count(terms.read_terms(TEST_EMAIL, "Cardio-Set")) == 3


def test_terms_new_collision(auth):
    auth.post("/terms/new", data={"new_name": "dup", "content": "a"})
    r = auth.post("/terms/new", data={"new_name": "dup", "content": "b"})
    assert r.status_code == 400 and "already exists" in r.text


def test_terms_new_empty_rejected(auth):
    r = auth.post("/terms/new", data={"new_name": "blank", "content": "   "})
    assert r.status_code == 400 and "at least one term" in r.text


def test_terms_edit_rename(auth):
    from web import terms
    auth.post("/terms/new", data={"new_name": "before", "content": "a\nb"})
    auth.post("/terms/before/edit", data={"new_name": "after", "content": "a\nb"})
    assert not terms.exists(TEST_EMAIL, "before")
    assert terms.read_terms(TEST_EMAIL, "after").strip() == "a\nb"


def test_terms_edit_collision(auth):
    from web import terms
    auth.post("/terms/new", data={"new_name": "a1", "content": "x"})
    auth.post("/terms/new", data={"new_name": "a2", "content": "y"})
    r = auth.post("/terms/a1/edit", data={"new_name": "a2", "content": "x"})
    assert r.status_code == 400 and "already exists" in r.text
    assert terms.read_terms(TEST_EMAIL, "a2").strip() == "y"  # untouched


def test_terms_edit_missing_redirects(auth):
    r = auth.get("/terms/nope/edit", follow_redirects=False)
    assert r.status_code == 302 and "error=not_found" in r.headers["location"]


def test_terms_duplicate(auth):
    from web import terms
    auth.post("/terms/new", data={"new_name": "orig", "content": "a\nb"})
    auth.post("/terms/orig/duplicate")
    assert terms.exists(TEST_EMAIL, "orig-copy")


def test_terms_delete(auth):
    from web import terms
    auth.post("/terms/new", data={"new_name": "gone", "content": "a"})
    auth.post("/terms/delete", data={"name": "gone"})
    assert not terms.exists(TEST_EMAIL, "gone")


# ── Pure helpers ────────────────────────────────────────────────────────────

def test_default_report_name_format():
    from web.app import _safe_report_name
    assert re.fullmatch(r"report-\d{8}-\d{6}", _safe_report_name(""))
    assert _safe_report_name("My Report.html") == "My-Report"


def test_rows_from_samples_collapses_and_pairs():
    from web.app import _rows_from_samples
    # Each field is one sample; internal line breaks collapse to single spaces.
    rows = _rows_from_samples(
        ["the patient\nhas  hypertension", "second sample"],
        ["the patient has hypotension", "second\tsample"],
    )
    assert rows == [
        ("the patient has hypertension", "the patient has hypotension"),
        ("second sample", "second sample"),
    ]


def test_rows_from_samples_skips_blank_rows():
    from web.app import _rows_from_samples
    # A trailing fully-empty row (e.g. one the user added but didn't fill) is dropped.
    assert _rows_from_samples(["a", "   ", ""], ["x", "", ""]) == [("a", "x")]


def test_rows_from_samples_missing_side_errors():
    from web.app import _rows_from_samples
    with pytest.raises(ValueError, match="Sample 1 is missing its generated"):
        _rows_from_samples(["only ref"], [""])
    with pytest.raises(ValueError, match="at least one sample"):
        _rows_from_samples(["", ""], ["", ""])


def test_rows_from_csv_aliases_and_missing_cols():
    from web.app import _rows_from_csv
    rows = _rows_from_csv(b"reference,hypothesis\nfoo,bar\n")
    assert rows == [("foo", "bar")]
    with pytest.raises(ValueError):
        _rows_from_csv(b"x,y\n1,2\n")


def test_decode_upload_tolerates_encodings():
    from web.app import _decode_upload
    # UTF-8 (with and without BOM) is decoded cleanly, BOM stripped.
    assert _decode_upload("98.6°F café —".encode("utf-8")) == "98.6°F café —"
    assert _decode_upload("ref,gen".encode("utf-8-sig")) == "ref,gen"
    # Windows-1252 exports (Excel) keep their special characters instead of
    # collapsing to U+FFFD replacement characters.
    assert _decode_upload("98.6°F café —“x”".encode("cp1252")) == "98.6°F café —“x”"
    # Bytes undefined in every attempted codec still yield a string.
    assert "end" in _decode_upload(b"ok\x81end")


def test_rows_from_csv_cp1252():
    from web.app import _rows_from_csv
    rows = _rows_from_csv("ref,gen\ntemp 98.6°F,temp 100°F\n".encode("cp1252"))
    assert rows == [("temp 98.6°F", "temp 100°F")]


def test_read_rows_caps_examples(monkeypatch):
    import asyncio
    import web.app as app
    monkeypatch.setattr(app, "MAX_ROWS", 2)
    with pytest.raises(ValueError):
        asyncio.run(app._read_rows("paste", ["a", "b", "c"], ["x", "y", "z"], None))
