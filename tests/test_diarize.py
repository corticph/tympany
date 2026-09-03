"""Tests for Corti diarized transcript parsing."""

import json

import pytest

from tympany.diarize import (
    SpeakerSegment,
    Turn,
    build_word_speakers,
    distinct_speakers,
    flatten_segments,
    group_segments_by_turn,
    is_diarized,
    parse_corti_transcript,
    parse_corti_transcript_json,
    split_into_turns,
)


STREAMS_MSG = {
    "type": "transcript",
    "data": [
        {
            "id": "uuid-1",
            "transcript": "Hello, what brings you in today?",
            "final": True,
            "speakerId": 0,
            "participant": {"channel": 0},
            "time": {"start": 6.50, "end": 8.90},
        },
        {
            "id": "uuid-2",
            "transcript": "I've had a fever and a cough.",
            "final": True,
            "speakerId": 1,
            "participant": {"channel": 0},
            "time": {"start": 3.40, "end": 6.20},
        },
    ],
}

REST_MSG = {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "metadata": {
        "participantsRoles": [
            {"channel": 0, "role": "doctor"},
            {"channel": 1, "role": "patient"},
        ]
    },
    "transcripts": [
        {"channel": 0, "participant": 0, "speakerId": 0,
         "text": "Hello, what brings you in today?",
         "start": 400, "end": 3100},
        {"channel": 1, "participant": 1, "speakerId": 1,
         "text": "I've had a fever and a cough.",
         "start": 3400, "end": 6200},
    ],
    "usageInfo": {"creditsConsumed": 0.42},
    "recordingId": "abc12300-0000-0000-0000-000000000001",
    "status": "completed",
}

NO_DIARIZ_MSG = {
    "type": "transcript",
    "data": [
        {
            "id": "uuid-0",
            "transcript": "Patient presents with fever and cough.",
            "final": True,
            "speakerId": -1,
            "participant": {"channel": 0},
            "time": {"start": 1.71, "end": 11.296},
        }
    ],
}


def test_parse_streams_sorts_by_start_time():
    segments = parse_corti_transcript(STREAMS_MSG)
    assert len(segments) == 2
    assert segments[0].start < segments[1].start
    assert segments[0].text == "I've had a fever and a cough."
    assert segments[1].text == "Hello, what brings you in today?"


def test_parse_streams_fields():
    segments = parse_corti_transcript(STREAMS_MSG)
    seg = segments[0]
    assert seg.speaker_id == 1
    assert seg.channel == 0
    assert seg.start == 3.40
    assert seg.end == 6.20


def test_parse_rest_converts_ms_to_seconds():
    segments = parse_corti_transcript(REST_MSG)
    assert len(segments) == 2
    assert segments[0].start == 0.400
    assert segments[0].end == 3.100
    assert segments[0].text == "Hello, what brings you in today?"
    assert segments[0].speaker_id == 0
    assert segments[0].channel == 0


def test_parse_rest_second_segment():
    segments = parse_corti_transcript(REST_MSG)
    seg = segments[1]
    assert seg.speaker_id == 1
    assert seg.channel == 1
    assert seg.start == 3.400
    assert seg.end == 6.200


def test_parse_json_string():
    raw = json.dumps(STREAMS_MSG)
    segments = parse_corti_transcript_json(raw)
    assert len(segments) == 2


def test_parse_json_array_treated_as_streams():
    raw = json.dumps(STREAMS_MSG["data"])
    segments = parse_corti_transcript_json(raw)
    assert len(segments) == 2


def test_parse_invalid_json():
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_corti_transcript_json("{not json}")


def test_parse_unrecognized_format():
    with pytest.raises(ValueError, match="Unrecognized Corti transcript format"):
        parse_corti_transcript({"foo": "bar"})


def test_speaker_label_diarized():
    seg = SpeakerSegment(speaker_id=0, channel=0, text="hi", start=0, end=1)
    assert seg.label == "Speaker 0"


def test_speaker_label_no_diarization():
    seg = SpeakerSegment(speaker_id=-1, channel=1, text="hi", start=0, end=1)
    assert seg.label == "Channel 1"


def test_flatten_segments():
    segments = parse_corti_transcript(STREAMS_MSG)
    text = flatten_segments(segments)
    assert "fever" in text
    assert "Hello" in text
    assert text == "I've had a fever and a cough. Hello, what brings you in today?"


def test_flatten_segments_skips_empty():
    segments = [
        SpeakerSegment(0, 0, "", 0, 1),
        SpeakerSegment(0, 0, "hello", 1, 2),
    ]
    assert flatten_segments(segments) == "hello"


def test_build_word_speakers():
    segments = parse_corti_transcript(STREAMS_MSG)
    labels = build_word_speakers(segments)
    assert len(labels) == 13
    assert labels[0] == "Speaker 1"
    assert labels[7] == "Speaker 0"


def test_is_diarized_true():
    segments = parse_corti_transcript(STREAMS_MSG)
    assert is_diarized(segments) is True


def test_is_diarized_false():
    segments = parse_corti_transcript(NO_DIARIZ_MSG)
    assert is_diarized(segments) is False


def test_distinct_speakers():
    segments = parse_corti_transcript(STREAMS_MSG)
    speakers = distinct_speakers(segments)
    assert speakers == ["Speaker 1", "Speaker 0"]


def test_distinct_speakers_no_diarization():
    segments = parse_corti_transcript(NO_DIARIZ_MSG)
    speakers = distinct_speakers(segments)
    assert speakers == ["Channel 0"]


# ---------------------------------------------------------------------------
# split_into_turns
# ---------------------------------------------------------------------------

def test_split_into_turns_alternating_speakers():
    segments = [
        SpeakerSegment(0, 0, "Hello doctor", 0, 2),
        SpeakerSegment(1, 0, "Hi there", 2, 4),
        SpeakerSegment(0, 0, "How are you", 4, 6),
    ]
    turns = split_into_turns(segments)
    assert len(turns) == 3
    assert turns[0] == Turn("Speaker 0", "Hello doctor")
    assert turns[1] == Turn("Speaker 1", "Hi there")
    assert turns[2] == Turn("Speaker 0", "How are you")


def test_split_into_turns_merges_consecutive_same_speaker():
    segments = [
        SpeakerSegment(0, 0, "Hello", 0, 1),
        SpeakerSegment(0, 0, "doctor", 1, 2),
        SpeakerSegment(1, 0, "Hi", 2, 3),
    ]
    turns = split_into_turns(segments)
    assert len(turns) == 2
    assert turns[0] == Turn("Speaker 0", "Hello doctor")
    assert turns[1] == Turn("Speaker 1", "Hi")


def test_split_into_turns_non_diarized_single_turn():
    segments = [
        SpeakerSegment(-1, 0, "Hello", 0, 1),
        SpeakerSegment(-1, 0, "world", 1, 2),
    ]
    turns = split_into_turns(segments)
    assert len(turns) == 1
    assert turns[0] == Turn("Channel 0", "Hello world")


def test_split_into_turns_skips_empty_segments():
    segments = [
        SpeakerSegment(0, 0, "Hello", 0, 1),
        SpeakerSegment(0, 0, "", 1, 2),
        SpeakerSegment(1, 0, "Hi", 2, 3),
    ]
    turns = split_into_turns(segments)
    assert len(turns) == 2
    assert turns[0] == Turn("Speaker 0", "Hello")
    assert turns[1] == Turn("Speaker 1", "Hi")


def test_split_into_turns_empty_input():
    assert split_into_turns([]) == []


def test_parse_streams_top_level_channel():
    """Minimal ref format: channel at top level, no participant wrapper."""
    data = {"type": "transcript", "data": [
        {"transcript": "Hello", "speakerId": 0, "channel": 1},
    ]}
    segments = parse_corti_transcript(data)
    assert segments[0].channel == 1


# ---------------------------------------------------------------------------
# group_segments_by_turn
# ---------------------------------------------------------------------------

def test_group_segments_by_turn_alternating():
    segments = [
        SpeakerSegment(0, 0, "Hello", 0, 1),
        SpeakerSegment(1, 0, "Hi", 1, 2),
        SpeakerSegment(0, 0, "Bye", 2, 3),
    ]
    groups = group_segments_by_turn(segments)
    assert len(groups) == 3
    assert [s.text for s in groups[0]] == ["Hello"]
    assert [s.text for s in groups[1]] == ["Hi"]
    assert [s.text for s in groups[2]] == ["Bye"]


def test_group_segments_by_turn_merges_consecutive():
    segments = [
        SpeakerSegment(0, 0, "Hello", 0, 1),
        SpeakerSegment(0, 0, "doctor", 1, 2),
        SpeakerSegment(1, 0, "Hi", 2, 3),
    ]
    groups = group_segments_by_turn(segments)
    assert len(groups) == 2
    assert [s.text for s in groups[0]] == ["Hello", "doctor"]
    assert [s.text for s in groups[1]] == ["Hi"]


def test_group_segments_by_turn_skips_empty():
    segments = [
        SpeakerSegment(0, 0, "Hello", 0, 1),
        SpeakerSegment(0, 0, "", 1, 2),
        SpeakerSegment(1, 0, "Hi", 2, 3),
    ]
    groups = group_segments_by_turn(segments)
    assert len(groups) == 2
    assert [s.text for s in groups[0]] == ["Hello"]
    assert [s.text for s in groups[1]] == ["Hi"]


def test_group_segments_by_turn_empty():
    assert group_segments_by_turn([]) == []


def test_group_segments_matches_split_into_turns():
    """group_segments_by_turn and split_into_turns must produce the same grouping."""
    segments = [
        SpeakerSegment(0, 0, "Hello doctor", 0, 2),
        SpeakerSegment(1, 0, "Hi there", 2, 4),
        SpeakerSegment(1, 0, "how are you", 4, 6),
        SpeakerSegment(0, 0, "Goodbye", 6, 8),
    ]
    turns = split_into_turns(segments)
    groups = group_segments_by_turn(segments)
    assert len(turns) == len(groups)
    for turn, group in zip(turns, groups):
        assert turn.speaker == group[0].label
        assert turn.text == " ".join(s.text.strip() for s in group)
