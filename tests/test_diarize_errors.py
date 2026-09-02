"""Tests for diarization error detection (tympany.diarize_errors)."""

from tympany.diarize import SpeakerSegment
from tympany.diarize_errors import align_segments


def _seg(speaker_id, channel, text, start, end):
    return SpeakerSegment(speaker_id, channel, text, start, end)


def test_no_errors_when_segments_match():
    ref = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
        _seg(1, 0, "I have a fever", 3.4, 7.2),
    ]
    gen = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
        _seg(1, 0, "I have a fever", 3.4, 7.2),
    ]
    errors = align_segments(ref, gen)
    assert errors == []


def test_speaker_mismatch():
    ref = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
        _seg(1, 0, "I have a fever", 3.4, 7.2),
    ]
    gen = [
        _seg(1, 0, "hello doctor", 0.4, 3.1),
        _seg(0, 0, "I have a fever", 3.4, 7.2),
    ]
    errors = align_segments(ref, gen)
    mismatches = [e for e in errors if e.category == "speaker_mismatch"]
    assert len(mismatches) == 2
    assert "Speaker 0" in mismatches[0].detail or "Speaker 1" in mismatches[0].detail


def test_merged_turns():
    ref = [
        _seg(0, 0, "how are you", 0.0, 3.0),
        _seg(1, 0, "I am fine", 3.0, 6.0),
    ]
    gen = [
        _seg(0, 0, "how are you I am fine", 0.0, 6.0),
    ]
    errors = align_segments(ref, gen)
    merges = [e for e in errors if e.category == "speaker_merge"]
    assert len(merges) == 1
    assert "Merged turns" in merges[0].detail
    assert "how are you" in merges[0].ref_text
    assert "I am fine" in merges[0].ref_text


def test_split_turn():
    ref = [
        _seg(0, 0, "how are you I am fine", 0.0, 6.0),
    ]
    gen = [
        _seg(0, 0, "how are you", 0.0, 3.0),
        _seg(1, 0, "I am fine", 3.0, 6.0),
    ]
    errors = align_segments(ref, gen)
    splits = [e for e in errors if e.category == "speaker_split"]
    assert len(splits) == 1
    assert "Split turn" in splits[0].detail
    assert "how are you I am fine" in splits[0].ref_text


def test_missing_turn():
    ref = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
        _seg(1, 0, "I have a fever", 3.4, 7.2),
        _seg(0, 0, "get well soon", 8.0, 10.0),
    ]
    gen = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
        _seg(1, 0, "I have a fever", 3.4, 7.2),
    ]
    errors = align_segments(ref, gen)
    missing = [e for e in errors if e.category == "missing_turn"]
    assert len(missing) == 1
    assert "get well soon" in missing[0].ref_text
    assert missing[0].gen_text == ""


def test_extra_turn():
    ref = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
    ]
    gen = [
        _seg(0, 0, "hello doctor", 0.4, 3.1),
        _seg(1, 0, "extra words here", 4.0, 7.0),
    ]
    errors = align_segments(ref, gen)
    extra = [e for e in errors if e.category == "extra_turn"]
    assert len(extra) == 1
    assert "extra words here" in extra[0].gen_text
    assert extra[0].ref_text == ""


def test_empty_segments_returns_no_errors():
    assert align_segments([], []) == []


def test_diarization_error_carries_speaker():
    ref = [_seg(0, 0, "hi", 0, 1)]
    gen = [_seg(1, 0, "hi", 0, 1)]
    errors = align_segments(ref, gen)
    assert errors
    assert errors[0].speaker != ""
