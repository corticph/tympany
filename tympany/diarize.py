"""Parse Corti diarized transcript JSON into speaker-tagged segments.

Corti exposes two transcript shapes:

- **Streams** (WebSocket): ``{ type: "transcript", data: [{ id, transcript,
  final, speakerId, participant: { channel }, time: { start, end } }] }``
  Segments arrive out of order; must be sorted by ``time.start``.

- **REST** (``/transcripts``): ``{ id, metadata: { participantsRoles },
  transcripts: [{ channel, participant, speakerId, text, start, end }], ... }``
  Already ordered; times are in milliseconds.

``parse_corti_transcript`` auto-detects the format. ``flatten_segments`` joins
segment texts into a single string for bewer evaluation. ``build_word_speakers``
produces a per-word speaker label list so ``parser.from_bewer`` can tag each
token with its speaker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeakerSegment:
    speaker_id: int
    channel: int
    text: str
    start: float
    end: float

    @property
    def label(self) -> str:
        if self.speaker_id < 0:
            return f"Channel {self.channel}"
        return f"Speaker {self.speaker_id}"


def _detect_format(data: dict) -> str:
    if data.get("type") == "transcript":
        return "streams"
    if "transcripts" in data:
        return "rest"
    raise ValueError(
        "Unrecognized Corti transcript format — expected a streams message "
        '(type: "transcript") or a REST response (with a "transcripts" key).'
    )


def _parse_streams(data: dict) -> list[SpeakerSegment]:
    segments: list[SpeakerSegment] = []
    for seg in data.get("data", []):
        speaker_id = int(seg.get("speakerId", -1))
        participant = seg.get("participant") or {}
        channel = int(participant.get("channel", 0))
        text = seg.get("transcript") or ""
        time = seg.get("time") or {}
        start = float(time.get("start", 0))
        end = float(time.get("end", 0))
        segments.append(SpeakerSegment(speaker_id, channel, text, start, end))
    segments.sort(key=lambda s: (s.start, s.end))
    return segments


def _parse_rest(data: dict) -> list[SpeakerSegment]:
    segments: list[SpeakerSegment] = []
    for seg in data.get("transcripts") or []:
        speaker_id = int(seg.get("speakerId", -1))
        channel = int(seg.get("channel", 0))
        text = seg.get("text") or ""
        start = float(seg.get("start", 0)) / 1000.0
        end = float(seg.get("end", 0)) / 1000.0
        segments.append(SpeakerSegment(speaker_id, channel, text, start, end))
    return segments


def parse_corti_transcript(data: dict) -> list[SpeakerSegment]:
    fmt = _detect_format(data)
    if fmt == "streams":
        return _parse_streams(data)
    return _parse_rest(data)


def parse_corti_transcript_json(raw: str) -> list[SpeakerSegment]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if isinstance(data, list):
        data = {"type": "transcript", "data": data}
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object or array of transcript segments.")
    return parse_corti_transcript(data)


def flatten_segments(segments: list[SpeakerSegment]) -> str:
    return " ".join(s.text for s in segments if s.text.strip())


def build_word_speakers(segments: list[SpeakerSegment]) -> list[str]:
    labels: list[str] = []
    for seg in segments:
        for _ in seg.text.split():
            labels.append(seg.label)
    return labels


def is_diarized(segments: list[SpeakerSegment]) -> bool:
    return any(s.speaker_id >= 0 for s in segments)


def distinct_speakers(segments: list[SpeakerSegment]) -> list[str]:
    seen: list[str] = []
    for seg in segments:
        if seg.label not in seen:
            seen.append(seg.label)
    return seen
