"""Tests for the Canal-style report renderer (web.report_render)."""

from __future__ import annotations

from web import report_render


def _op(type_, ref=None, hyp=None):
    return {"type": type_, "ref": ref, "hyp": hyp}


def _envelope(ops, *, key_terms=None):
    return {
        "metrics": {"wer": "10.00%", "cer": "5.00%", "mtr": "50.00%" if key_terms else None},
        "settings": {"normalization": True, "medical_terms": bool(key_terms),
                     "key_terms": key_terms or []},
        "examples": [{"example": 1, "ref": "", "hyp": "", "ops": ops}],
    }


def test_op_colors_and_padding():
    """Each op type maps to its Canal colour, shorter side padded to align."""
    ops = [
        _op("MATCH", "the", "the"),
        _op("SUBSTITUTE", "quick", "quik"),
        _op("DELETE", "brown"),
        _op("INSERT", hyp="now"),
    ]
    view = report_render.canal_view(_envelope(ops))
    line = view["examples"][0]["lines"][0]
    ref, gen = str(line["ref"]), str(line["gen"])

    assert report_render.COLOR_CORRECT in ref  # MATCH
    assert report_render.COLOR_MISSPELL in ref and report_render.COLOR_MISSPELL in gen  # SUBSTITUTE
    assert report_render.COLOR_MISSING in ref  # DELETE on ref side
    assert report_render.COLOR_EXTRA in gen    # INSERT on gen side
    # The deleted word leaves padding on the generated row; the inserted word
    # leaves padding on the reference row.
    assert report_render.COLOR_PADDING in gen
    assert report_render.COLOR_PADDING in ref


def test_corpus_counts_from_ops():
    ops = [_op("MATCH", "a", "a"), _op("DELETE", "bb"), _op("INSERT", hyp="ccc")]
    view = report_render.canal_view(_envelope(ops))
    s = view["summary"]
    assert s["ref_words"] == "2" and s["ref_chars"] == "3"   # a + bb
    assert s["gen_words"] == "2" and s["gen_chars"] == "4"   # a + ccc


def test_no_key_terms_means_no_boxing_or_legend():
    ops = [_op("MATCH", "chest", "chest"), _op("MATCH", "pain", "pain")]
    view = report_render.canal_view(_envelope(ops))
    assert view["medical_terms"] is False
    assert "keyword-box" not in str(view["examples"][0]["lines"][0]["ref"])


def test_single_word_term_boxed_on_both_rows():
    ops = [_op("MATCH", "the", "the"), _op("MATCH", "fever", "fever")]
    view = report_render.canal_view(_envelope(ops, key_terms=["fever"]))
    assert view["medical_terms"] is True
    line = view["examples"][0]["lines"][0]
    assert str(line["ref"]).count("keyword-box") == 1
    assert str(line["gen"]).count("keyword-box") == 1


def test_multiword_term_wrapped_in_single_box():
    ops = [
        _op("MATCH", "denies", "denies"),
        _op("MATCH", "shortness", "shortness"),
        _op("MATCH", "of", "of"),
        _op("MATCH", "breath", "breath"),
    ]
    view = report_render.canal_view(_envelope(ops, key_terms=["shortness of breath"]))
    ref = str(view["examples"][0]["lines"][0]["ref"])
    # One box spanning the three term words, with their words inside it.
    assert ref.count("keyword-box") == 1
    assert "shortness" in ref and "breath" in ref


def test_term_only_boxed_where_it_actually_appears():
    """A substituted term is boxed on the side that holds the real term word."""
    ops = [_op("SUBSTITUTE", "dizziness", "dizzyness")]
    view = report_render.canal_view(_envelope(ops, key_terms=["dizziness"]))
    line = view["examples"][0]["lines"][0]
    assert "keyword-box" in str(line["ref"])      # ref has the term
    assert "keyword-box" not in str(line["gen"])  # generated misspelling does not match


def test_bewer_slices_drive_highlighting():
    """When the envelope carries bewer's key-term slices, box exactly those."""
    ops = [
        _op("MATCH", "non", "non"),
        _op("MATCH", "st", "st"),
        _op("MATCH", "elevation", "elevation"),
        _op("MATCH", "today", "today"),
    ]
    env = _envelope(ops, key_terms=["irrelevant"])
    # [0,3) = "non st elevation"; the string fallback could never match this from
    # the raw term, but the slice does.
    env["examples"][0]["ref_key_terms"] = [[0, 3]]
    env["examples"][0]["hyp_key_terms"] = []
    view = report_render.canal_view(env)
    line = view["examples"][0]["lines"][0]
    assert str(line["ref"]).count("keyword-box") == 1   # one box over three words
    assert str(line["gen"]).count("keyword-box") == 0   # no slices for gen


def test_slices_take_precedence_over_fallback():
    """An empty slice list means 'bewer found nothing here' — not 'fall back'."""
    ops = [_op("MATCH", "fever", "fever")]
    env = _envelope(ops, key_terms=["fever"])
    env["examples"][0]["ref_key_terms"] = []   # present but empty
    env["examples"][0]["hyp_key_terms"] = []
    view = report_render.canal_view(env)
    assert "keyword-box" not in str(view["examples"][0]["lines"][0]["ref"])


def test_adjacent_terms_get_separate_boxes():
    ops = [_op("MATCH", "nausea", "nausea"), _op("MATCH", "dizziness", "dizziness")]
    view = report_render.canal_view(_envelope(ops, key_terms=["nausea", "dizziness"]))
    assert str(view["examples"][0]["lines"][0]["ref"]).count("keyword-box") == 2


# ---------------------------------------------------------------------------
# Per-speaker splitting (Phase 2)
# ---------------------------------------------------------------------------

def _envelope_with_speakers(ops, ref_spk, hyp_spk, *, key_terms=None):
    env = _envelope(ops, key_terms=key_terms)
    env["examples"][0]["ref_speakers"] = ref_spk
    env["examples"][0]["hyp_speakers"] = hyp_spk
    return env


def test_split_ops_by_speaker_two_speakers():
    ops = [
        _op("MATCH", "hello", "hello"),
        _op("MATCH", "doctor", "doctor"),
        _op("MATCH", "I", "I"),
        _op("MATCH", "see", "see"),
    ]
    ref_spk = ["Speaker 0", "Speaker 0", "Speaker 1", "Speaker 1"]
    hyp_spk = ["Speaker 0", "Speaker 0", "Speaker 1", "Speaker 1"]
    ex = {"ops": ops, "ref_speakers": ref_spk, "hyp_speakers": hyp_spk}
    groups = report_render._split_ops_by_speaker(ex)
    assert len(groups) == 2
    assert groups[0][0] == "Speaker 0"
    assert len(groups[0][1]) == 2
    assert groups[1][0] == "Speaker 1"
    assert len(groups[1][1]) == 2


def test_canal_view_splits_examples_by_speaker():
    ops = [
        _op("MATCH", "hello", "hello"),
        _op("MATCH", "doctor", "doctor"),
        _op("MATCH", "I", "I"),
        _op("MATCH", "see", "see"),
    ]
    ref_spk = ["Speaker 0", "Speaker 0", "Speaker 1", "Speaker 1"]
    hyp_spk = ["Speaker 0", "Speaker 0", "Speaker 1", "Speaker 1"]
    view = report_render.canal_view(_envelope_with_speakers(ops, ref_spk, hyp_spk))
    assert len(view["examples"]) == 2
    assert view["examples"][0]["speaker"] == "Speaker 0"
    assert view["examples"][1]["speaker"] == "Speaker 1"
    assert len(view["examples"][0]["lines"]) > 0
    assert len(view["examples"][1]["lines"]) > 0


def test_canal_view_no_speakers_stays_single_example():
    ops = [_op("MATCH", "hello", "hello"), _op("MATCH", "doctor", "doctor")]
    view = report_render.canal_view(_envelope(ops))
    assert len(view["examples"]) == 1
    assert view["examples"][0]["speaker"] == ""


def test_canal_view_speaker_split_preserves_word_counts():
    """Splitting by speaker doesn't change the summary corpus counts."""
    ops = [
        _op("MATCH", "hello", "hello"),
        _op("MATCH", "doctor", "doctor"),
        _op("MATCH", "I", "I"),
        _op("MATCH", "see", "see"),
    ]
    ref_spk = ["Speaker 0", "Speaker 0", "Speaker 1", "Speaker 1"]
    hyp_spk = ["Speaker 0", "Speaker 0", "Speaker 1", "Speaker 1"]
    view = report_render.canal_view(_envelope_with_speakers(ops, ref_spk, hyp_spk))
    assert view["summary"]["ref_words"] == "4"
    assert view["summary"]["gen_words"] == "4"


def test_canal_view_per_speaker_metrics():
    env = _envelope([_op("MATCH", "hi", "hi")])
    env["per_speaker"] = {
        "Speaker 0": {
            "metrics": {"wer": "0.00%", "cer": "0.00%", "mtr": None},
            "ref_words": 2,
            "gen_words": 2,
        },
        "Speaker 1": {
            "metrics": {"wer": "50.00%", "cer": "25.00%", "mtr": None},
            "ref_words": 4,
            "gen_words": 4,
        },
    }
    view = report_render.canal_view(env)
    assert view["show_per_speaker"] is True
    rows = view["per_speaker_rows"]
    assert len(rows) == 2
    assert rows[0]["label"] == "Speaker 0"
    assert rows[0]["wer"] == "0.00%"
    assert rows[0]["ref_words"] == "2"
    assert rows[1]["label"] == "Speaker 1"
    assert rows[1]["wer"] == "50.00%"
    assert rows[1]["cer"] == "25.00%"


def test_canal_view_no_per_speaker_when_absent():
    view = report_render.canal_view(_envelope([_op("MATCH", "hi", "hi")]))
    assert view["show_per_speaker"] is False
    assert view["per_speaker_rows"] == []
