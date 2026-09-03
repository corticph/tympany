"""Tests for the bewer JSON envelope → Sample/DiffGroup mapping.

`tests/fixtures/bewer_sample.json` is a captured bewer envelope (from
`bewer_eval.run_bewer`). These map/metric tests run against it with no bewer
import — fast and deterministic. A separate, skippable test exercises the live
`run_bewer` when bewer is installed.

Regenerate the fixture:
    poetry run python -c "import json; from tympany.bewer_eval import run_bewer; \
json.dump(run_bewer([('the patient has hypertension today','the patient has hypotension'), \
('blood pressure was elevated','blood pressure was elevated')], medical_terms=['hypertension','blood pressure']), \
open('tests/fixtures/bewer_sample.json','w'), indent=1)"
"""

import json
import re
from pathlib import Path

import pytest

from tympany.categorize import categorize_group
from tympany.parser import from_bewer, metrics_from_bewer, reference_corpus

ENVELOPE = json.loads((Path(__file__).parent / "fixtures" / "bewer_sample.json").read_text())
_PCT = re.compile(r"\d+(\.\d+)?%")


def test_from_bewer_builds_samples_and_diffs():
    samples = from_bewer(ENVELOPE)
    assert len(samples) == 2
    ex1 = samples[0]
    assert " ".join(t.text for t in ex1.ref_tokens) == "the patient has hypertension today"
    # the substitution/deletion run is captured as a diff group with hypotension on the gen side
    assert ex1.diffs and "hypotension" in ex1.diffs[0].pred
    assert samples[1].diffs == ()  # identical example → no diffs


def test_from_bewer_tags_tokens_with_speakers():
    ref_ws = [["Speaker 0", "Speaker 0", "Speaker 0", "Speaker 0", "Speaker 1"]]
    gen_ws = [["Speaker 0", "Speaker 0", "Speaker 0", "Speaker 1"]]
    samples = from_bewer(ENVELOPE, ref_word_speakers=ref_ws, gen_word_speakers=gen_ws)
    assert len(samples) == 2
    ex = samples[0]
    assert ex.ref_tokens[0].speaker == "Speaker 0"
    assert ex.ref_tokens[4].speaker == "Speaker 1"
    assert ex.pred_tokens[0].speaker == "Speaker 0"
    assert ex.pred_tokens[3].speaker == "Speaker 1"
    assert "Speaker 1" in ex.speakers
    assert "Speaker 0" in ex.speakers
    assert ex.diffs
    assert ex.diffs[0].speaker


def test_from_bewer_reads_speakers_from_envelope():
    import copy
    env = copy.deepcopy(ENVELOPE)
    env["examples"][0]["ref_speakers"] = ["Speaker 0"] * 5
    env["examples"][0]["hyp_speakers"] = ["Speaker 0"] * 4
    samples = from_bewer(env)
    assert samples[0].ref_tokens[0].speaker == "Speaker 0"
    assert "Speaker 0" in samples[0].speakers


def test_from_bewer_no_speakers_backward_compat():
    samples = from_bewer(ENVELOPE)
    assert all(t.speaker == "" for s in samples for t in s.ref_tokens)
    assert all(t.speaker == "" for s in samples for t in s.pred_tokens)
    assert all(s.speakers == () for s in samples)


def test_samples_to_payload_includes_speaker():
    from tympany.parser import samples_to_payload
    ref_ws = [["Speaker 0", "Speaker 0", "Speaker 0", "Speaker 0", "Speaker 1"]]
    gen_ws = [["Speaker 0", "Speaker 0", "Speaker 0", "Speaker 1"]]
    samples = from_bewer(ENVELOPE, ref_word_speakers=ref_ws, gen_word_speakers=gen_ws)
    payload = samples_to_payload(samples)
    assert "speaker" in payload[0]["ref_tokens"][0]
    assert "speakers" in payload[0]
    assert payload[0]["ref_tokens"][0]["speaker"] == "Speaker 0"
    assert "Speaker 1" in payload[0]["speakers"]


def test_from_bewer_diffs_feed_categorizer():
    samples = from_bewer(ENVELOPE)
    edits = [e for d in samples[0].diffs for e in categorize_group(d)]
    assert edits
    assert edits[0].classification in {
        "formatting_error", "replacement_candidate", "context_dependent", "misrecognition",
    }


def test_categorize_group_preserves_speaker():
    from tympany.parser import DiffGroup
    group = DiffGroup(("hello",), ("helloo",), speaker="Speaker 1")
    edits = categorize_group(group)
    assert edits
    assert edits[0].speaker == "Speaker 1"


def test_metrics_from_bewer():
    m = metrics_from_bewer(ENVELOPE)
    assert _PCT.fullmatch(m["wer"]) and _PCT.fullmatch(m["cer"]) and _PCT.fullmatch(m["mtr"])
    assert m["normalization"] is True


def test_metrics_from_bewer_includes_per_speaker():
    env = {
        "metrics": {"wer": "10.00%", "cer": "5.00%", "mtr": None},
        "settings": {"normalization": True, "diarized": True},
        "per_speaker": {
            "Speaker 0": {
                "metrics": {"wer": "0.00%", "cer": "0.00%", "mtr": None},
                "ref_words": 6,
                "gen_words": 6,
            },
        },
    }
    m = metrics_from_bewer(env)
    assert "per_speaker" in m
    assert m["per_speaker"]["Speaker 0"]["wer"] == "0.00%"
    assert m["per_speaker"]["Speaker 0"]["ref_words"] == 6


def test_metrics_from_bewer_no_per_speaker():
    m = metrics_from_bewer(ENVELOPE)
    assert "per_speaker" not in m


def test_metrics_from_bewer_includes_diarization_accuracy():
    env = {
        "metrics": {"wer": "10.00%", "cer": "5.00%", "mtr": None},
        "settings": {"normalization": True, "diarized": True},
        "diarization_accuracy": {"accuracy": "75.00%", "matched": 3, "total": 4},
    }
    m = metrics_from_bewer(env)
    assert m["diarization_accuracy"] == "75.00%"
    assert m["diarization_matched"] == 3
    assert m["diarization_total"] == 4


def test_reference_corpus_from_bewer_samples():
    samples = from_bewer(ENVELOPE)
    assert reference_corpus(samples) == "the patient has hypertension today\nblood pressure was elevated"


def test_run_bewer_live():
    bewer = pytest.importorskip("bewer")  # noqa: F841 — skip if not installed
    from tympany.bewer_eval import run_bewer
    env = run_bewer(
        [("the patient has hypertension", "the patient has hypotension")],
        medical_terms=["hypertension"],
    )
    assert _PCT.fullmatch(env["metrics"]["wer"])
    assert env["examples"][0]["ops"]  # alignment ops present


def test_run_bewer_live_honors_normalization_flag():
    bewer = pytest.importorskip("bewer")  # noqa: F841 — skip if not installed
    from tympany.bewer_eval import run_bewer

    rows = [("Hello, Café.", "hello cafe")]
    normalized = run_bewer(rows, normalization=True)
    raw = run_bewer(rows, normalization=False)

    assert normalized["settings"]["normalization"] is True
    assert raw["settings"]["normalization"] is False
    assert normalized["metrics"]["wer"] == "0.00%"
    assert raw["metrics"]["wer"] == "100.00%"
    assert [op["type"] for op in raw["examples"][0]["ops"]] == [
        "SUBSTITUTE", "SUBSTITUTE",
    ]


def test_run_bewer_diarized_live():
    bewer = pytest.importorskip("bewer")
    from tympany.bewer_eval import run_bewer_diarized
    from tympany.diarize import SpeakerSegment

    ref_segs = [
        SpeakerSegment(0, 0, "Hello what brings you in today", 0.4, 3.1),
        SpeakerSegment(1, 0, "I have had a fever", 3.4, 7.2),
    ]
    gen_segs = [
        SpeakerSegment(0, 0, "Hello what brings you in today", 0.4, 3.1),
        SpeakerSegment(1, 0, "I have had a ever", 3.4, 7.2),
    ]
    env = run_bewer_diarized(ref_segs, gen_segs)

    assert "per_speaker" in env
    assert "Speaker 0" in env["per_speaker"]
    assert "Speaker 1" in env["per_speaker"]
    assert env["settings"]["diarized"] is True

    spk0 = env["per_speaker"]["Speaker 0"]
    spk1 = env["per_speaker"]["Speaker 1"]
    assert spk0["metrics"]["wer"] == "0.00%"
    assert spk1["metrics"]["wer"] != "0.00%"
    assert spk0["ref_words"] == 6
    assert spk1["ref_words"] == 5


def test_build_rows_per_speaker():
    from tympany.bewer_eval import build_rows_per_speaker

    samples = [{
        "example": 1,
        "ref_tokens": [
            {"cls": "ok", "text": "hello", "speaker": "Speaker 0"},
            {"cls": "sub", "text": "fever", "speaker": "Speaker 1"},
        ],
        "pred_tokens": [
            {"cls": "ok", "text": "hello", "speaker": "Speaker 0"},
            {"cls": "sub", "text": "ever", "speaker": "Speaker 1"},
        ],
    }]
    edits = [{
        "example": 1, "speaker": "Speaker 1",
        "ref": "fever", "gen": "ever",
        "excluded": True,
    }]
    speaker_rows = build_rows_per_speaker(samples, edits)
    assert "Speaker 0" in speaker_rows
    assert "Speaker 1" in speaker_rows
    # Speaker 0 had no errors → (ref, gen) identical
    spk0 = speaker_rows["Speaker 0"][0]
    assert spk0[0] == "hello"
    assert spk0[1] == "hello"
    # Speaker 1's error was excluded → corrected to ref
    spk1 = speaker_rows["Speaker 1"][0]
    assert spk1[0] == "fever"
    assert spk1[1] == "fever"


def test_run_bewer_diarized_includes_accuracy():
    bewer = pytest.importorskip("bewer")
    from tympany.bewer_eval import run_bewer_diarized
    from tympany.diarize import SpeakerSegment

    ref_segs = [
        SpeakerSegment(0, 0, "hello doctor", 0.0, 3.0),
        SpeakerSegment(1, 0, "I am fine", 3.0, 6.0),
    ]
    gen_segs = [
        SpeakerSegment(0, 0, "hello doctor", 0.0, 3.0),
        SpeakerSegment(0, 0, "I am fine", 3.0, 6.0),
    ]
    env = run_bewer_diarized(ref_segs, gen_segs)
    assert env["diarization_accuracy"] is not None
    assert env["diarization_accuracy"]["accuracy"] == "50.00%"
    assert env["diarization_accuracy"]["matched"] == 1
    assert env["diarization_accuracy"]["total"] == 2


def test_run_bewer_diarized_produces_per_turn_examples():
    bewer = pytest.importorskip("bewer")
    from tympany.bewer_eval import run_bewer_diarized
    from tympany.diarize import SpeakerSegment

    ref_segs = [
        SpeakerSegment(0, 0, "Hello what brings you in today", 0.4, 3.1),
        SpeakerSegment(1, 0, "I have had a fever", 3.4, 7.2),
        SpeakerSegment(0, 0, "I will prescribe antibiotics", 7.5, 10.8),
    ]
    gen_segs = [
        SpeakerSegment(0, 0, "Hello what brings you in today", 0.4, 3.1),
        SpeakerSegment(1, 0, "I have had a ever", 3.4, 7.2),
        SpeakerSegment(0, 0, "I will prescribe antibiotic", 7.5, 10.8),
    ]
    env = run_bewer_diarized(ref_segs, gen_segs)

    # Three turns (alternating speakers) → three examples
    assert len(env["examples"]) == 3
    assert env["examples"][0]["example"] == 1
    assert env["examples"][1]["example"] == 2
    assert env["examples"][2]["example"] == 3

    # Each example's speaker labels should be uniform within the example
    ref_spk_0 = env["examples"][0]["ref_speakers"]
    assert all(s == "Speaker 0" for s in ref_spk_0)
    ref_spk_1 = env["examples"][1]["ref_speakers"]
    assert all(s == "Speaker 1" for s in ref_spk_1)

    # Per-speaker metrics still present
    assert "Speaker 0" in env["per_speaker"]
    assert "Speaker 1" in env["per_speaker"]


def test_run_bewer_diarized_unequal_turn_counts():
    bewer = pytest.importorskip("bewer")
    from tympany.bewer_eval import run_bewer_diarized
    from tympany.diarize import SpeakerSegment

    ref_segs = [
        SpeakerSegment(0, 0, "Hello doctor", 0, 2),
        SpeakerSegment(1, 0, "Hi", 2, 3),
        SpeakerSegment(0, 0, "Goodbye", 3, 5),
    ]
    gen_segs = [
        SpeakerSegment(0, 0, "Hello doctor", 0, 2),
        SpeakerSegment(1, 0, "Hi", 2, 3),
    ]
    env = run_bewer_diarized(ref_segs, gen_segs)

    # Ref has 3 turns, gen has 2 → 3 examples (last is a full deletion)
    assert len(env["examples"]) == 3
    assert env["examples"][2]["ref"] == "Goodbye"
    assert env["examples"][2]["hyp"] == ""
