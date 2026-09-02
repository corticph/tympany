"""Detect diarization errors by aligning reference and generated speaker turns.

Given time-ordered speaker segments from both sides, we align them by time
overlap and check for:

- **Speaker mismatch** — a gen segment covers the same time range as a ref
  segment but attributes the speech to a different speaker.
- **Merged turns** — two or more ref segments (different speakers) are
  covered by a single gen segment.
- **Split turns** — one ref segment is covered by two or more gen segments
  attributed to different speakers.
- **Missing turn** — a ref segment has no overlapping gen segment.
- **Extra turn** — a gen segment has no overlapping ref segment.

Each error is returned as a :class:`DiarizationError` with a category
matching the ``_CATEGORY_META`` table in ``categorize.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diarize import SpeakerSegment


@dataclass(frozen=True)
class DiarizationError:
    category: str
    detail: str
    speaker: str
    ref_text: str
    gen_text: str
    start: float
    end: float


def _overlap(a: SpeakerSegment, b: SpeakerSegment) -> float:
    """Time overlap in seconds between two segments."""
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def _coverage(seg: SpeakerSegment, other: SpeakerSegment) -> float:
    """Fraction of ``seg``'s duration covered by ``other``."""
    dur = seg.end - seg.start
    if dur <= 0:
        return 0.0
    return _overlap(seg, other) / dur


def align_segments(
    ref_segments: list[SpeakerSegment],
    gen_segments: list[SpeakerSegment],
    *,
    coverage_threshold: float = 0.5,
) -> list[DiarizationError]:
    """Align ref and gen speaker turns by time overlap and detect diarization errors.

    Returns a list of :class:`DiarizationError` instances. When the segments
    are well-aligned (same speakers, same turns, no missing/extra), the list
    is empty.
    """
    errors: list[DiarizationError] = []
    if not ref_segments or not gen_segments:
        return errors

    # For each ref segment, find the best-matching gen segment by time overlap.
    used_gen: set[int] = set()
    ref_to_gen: dict[int, list[int]] = {}

    for ri, rseg in enumerate(ref_segments):
        best_overlap = 0.0
        best_gi = -1
        for gi, gseg in enumerate(gen_segments):
            ov = _overlap(rseg, gseg)
            if ov > best_overlap:
                best_overlap = ov
                best_gi = gi
        if best_gi >= 0 and _coverage(rseg, gen_segments[best_gi]) >= coverage_threshold:
            ref_to_gen.setdefault(best_gi, []).append(ri)

    # For each gen segment, find the best-matching ref segment (reverse direction).
    gen_to_ref: dict[int, list[int]] = {}
    for gi, gseg in enumerate(gen_segments):
        best_overlap = 0.0
        best_ri = -1
        for ri, rseg in enumerate(ref_segments):
            ov = _overlap(gseg, rseg)
            if ov > best_overlap:
                best_overlap = ov
                best_ri = ri
        if best_ri >= 0 and _coverage(gseg, ref_segments[best_ri]) >= coverage_threshold:
            gen_to_ref.setdefault(best_ri, []).append(gi)

    # Detect: merged turns (multiple ref → one gen), split turns (one ref → multiple gen),
    # and speaker mismatches (1:1 match but different speakers).
    for gi, ref_indices in ref_to_gen.items():
        gseg = gen_segments[gi]
        if len(ref_indices) > 1:
            ref_speakers = {ref_segments[ri].speaker_id for ri in ref_indices}
            if len(ref_speakers) > 1:
                ref_texts = [ref_segments[ri].text for ri in ref_indices]
                errors.append(DiarizationError(
                    category="speaker_merge",
                    detail=f"Merged turns: {len(ref_indices)} ref segments → 1 gen segment",
                    speaker=gseg.label,
                    ref_text=" | ".join(ref_texts),
                    gen_text=gseg.text,
                    start=min(ref_segments[ri].start for ri in ref_indices),
                    end=max(ref_segments[ri].end for ri in ref_indices),
                ))
                used_gen.add(gi)

    for ri, gen_indices in gen_to_ref.items():
        rseg = ref_segments[ri]
        if len(gen_indices) > 1:
            gen_speakers = {gen_segments[gi].speaker_id for gi in gen_indices}
            if len(gen_speakers) > 1:
                gen_texts = [gen_segments[gi].text for gi in gen_indices]
                errors.append(DiarizationError(
                    category="speaker_split",
                    detail=f"Split turn: 1 ref segment → {len(gen_indices)} gen segments",
                    speaker=rseg.label,
                    ref_text=rseg.text,
                    gen_text=" | ".join(gen_texts),
                    start=min(gen_segments[gi].start for gi in gen_indices),
                    end=max(gen_segments[gi].end for gi in gen_indices),
                ))
                for gi in gen_indices:
                    used_gen.add(gi)

    # Check 1:1 matches for speaker mismatch.
    for gi, ref_indices in ref_to_gen.items():
        if len(ref_indices) == 1 and gi not in used_gen:
            ri = ref_indices[0]
            rseg = ref_segments[ri]
            gseg = gen_segments[gi]
            if rseg.speaker_id >= 0 and gseg.speaker_id >= 0 and rseg.speaker_id != gseg.speaker_id:
                errors.append(DiarizationError(
                    category="speaker_mismatch",
                    detail=f"Speaker mismatch: ref {rseg.label} → gen {gseg.label}",
                    speaker=gseg.label,
                    ref_text=rseg.text,
                    gen_text=gseg.text,
                    start=rseg.start,
                    end=rseg.end,
                ))
                used_gen.add(gi)

    # Missing turns: ref segments not matched to any gen segment.
    matched_refs: set[int] = set()
    for ref_indices in ref_to_gen.values():
        matched_refs.update(ref_indices)
    for ri, rseg in enumerate(ref_segments):
        if ri not in matched_refs:
            errors.append(DiarizationError(
                category="missing_turn",
                detail=f"Missing turn: no generated segment for ref {rseg.label}",
                speaker=rseg.label,
                ref_text=rseg.text,
                gen_text="",
                start=rseg.start,
                end=rseg.end,
            ))

    # Extra turns: gen segments not matched to any ref segment.
    for gi, gseg in enumerate(gen_segments):
        if gi not in used_gen:
            if gi not in {gi2 for ref_indices in ref_to_gen.values() for gi2 in [gi]}:
                pass
            matched_as_secondary = any(gi in gen_indices for gen_indices in gen_to_ref.values())
            if not matched_as_secondary:
                errors.append(DiarizationError(
                    category="extra_turn",
                    detail=f"Extra turn: no reference segment for gen {gseg.label}",
                    speaker=gseg.label,
                    ref_text="",
                    gen_text=gseg.text,
                    start=gseg.start,
                    end=gseg.end,
                ))

    return errors
