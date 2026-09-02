"""Tympany's diff model and BeWER-envelope mapping.

``Token`` / ``DiffGroup`` / ``Sample`` are the shapes the categorizer consumes.
They are built from a bewer evaluation envelope (see ``tympany.bewer_eval``) via
``from_bewer`` — Tympany no longer parses any report HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    cls: str   # "ok" | "sub" | "del" | "ins"
    text: str
    speaker: str = ""


@dataclass(frozen=True)
class DiffGroup:
    ref: tuple[str, ...]
    pred: tuple[str, ...]
    speaker: str = ""

    @property
    def op(self) -> str:
        if not self.ref:
            return "ins"
        if not self.pred:
            return "del"
        return "sub"


@dataclass(frozen=True)
class Sample:
    file_stem: str
    example_num: int
    ref_tokens: tuple[Token, ...]
    pred_tokens: tuple[Token, ...]
    diffs: tuple[DiffGroup, ...]
    speakers: tuple[str, ...] = ()


def samples_to_payload(samples: list[Sample]) -> list[dict]:
    """Serialise parsed samples into the JSON-friendly form persisted in history.

    Only the full per-example token streams (with their class) are kept — that
    is everything the re-run needs to reconstruct reference text and a
    corrected generated text with excluded errors removed.
    """
    return [
        {
            "example": s.example_num,
            "ref_tokens": [{"cls": t.cls, "text": t.text, "speaker": t.speaker} for t in s.ref_tokens],
            "pred_tokens": [{"cls": t.cls, "text": t.text, "speaker": t.speaker} for t in s.pred_tokens],
            "speakers": list(s.speakers),
        }
        for s in samples
    ]


def from_bewer(
    envelope: dict,
    file_stem: str = "report",
    ref_word_speakers: Optional[list[list[str]]] = None,
    gen_word_speakers: Optional[list[list[str]]] = None,
) -> list[Sample]:
    """Map a bewer JSON envelope (see tympany.bewer_eval) into Samples.

    Each alignment op becomes ref/pred Tokens (MATCH→ok, SUBSTITUTE→sub,
    DELETE→del, INSERT→ins); contiguous non-match ops are grouped into
    DiffGroups, exactly the shape the categorizer consumes. This is the
    This is how Tympany turns a bewer evaluation into reviewable diffs.

    When ``ref_word_speakers`` and ``gen_word_speakers`` are provided (one
    list per example, one speaker label per word), tokens are tagged with
    their speaker and each DiffGroup carries the speaker of its first token.
    """
    samples: list[Sample] = []
    for ex_idx, ex in enumerate(envelope.get("examples", [])):
        ref_tokens: list[Token] = []
        pred_tokens: list[Token] = []
        diffs: list[DiffGroup] = []
        cur_ref: list[str] = []
        cur_pred: list[str] = []
        cur_speaker: str = ""

        ref_ws = ref_word_speakers[ex_idx] if ref_word_speakers and ex_idx < len(ref_word_speakers) else ex.get("ref_speakers")
        gen_ws = gen_word_speakers[ex_idx] if gen_word_speakers and ex_idx < len(gen_word_speakers) else ex.get("hyp_speakers")
        ref_idx = 0
        gen_idx = 0

        def _flush() -> None:
            nonlocal cur_speaker
            if cur_ref or cur_pred:
                diffs.append(DiffGroup(tuple(cur_ref), tuple(cur_pred), speaker=cur_speaker))
                cur_ref.clear()
                cur_pred.clear()
                cur_speaker = ""

        for op in ex.get("ops", []):
            op_type = (op.get("type") or "").upper()
            ref, hyp = op.get("ref"), op.get("hyp")
            if op_type == "MATCH":
                _flush()
                ref_spk = ref_ws[ref_idx] if ref_ws and ref_idx < len(ref_ws) else ""
                gen_spk = gen_ws[gen_idx] if gen_ws and gen_idx < len(gen_ws) else ""
                ref_tokens.append(Token("ok", ref, speaker=ref_spk))
                pred_tokens.append(Token("ok", hyp, speaker=gen_spk))
                ref_idx += 1
                gen_idx += 1
            elif op_type == "SUBSTITUTE":
                ref_spk = ref_ws[ref_idx] if ref_ws and ref_idx < len(ref_ws) else ""
                gen_spk = gen_ws[gen_idx] if gen_ws and gen_idx < len(gen_ws) else ""
                if not cur_speaker:
                    cur_speaker = ref_spk or gen_spk
                ref_tokens.append(Token("sub", ref, speaker=ref_spk))
                pred_tokens.append(Token("sub", hyp, speaker=gen_spk))
                cur_ref.append(ref)
                cur_pred.append(hyp)
                ref_idx += 1
                gen_idx += 1
            elif op_type == "DELETE":
                ref_spk = ref_ws[ref_idx] if ref_ws and ref_idx < len(ref_ws) else ""
                if not cur_speaker:
                    cur_speaker = ref_spk
                ref_tokens.append(Token("del", ref, speaker=ref_spk))
                cur_ref.append(ref)
                ref_idx += 1
            elif op_type == "INSERT":
                gen_spk = gen_ws[gen_idx] if gen_ws and gen_idx < len(gen_ws) else ""
                if not cur_speaker:
                    cur_speaker = gen_spk
                pred_tokens.append(Token("ins", hyp, speaker=gen_spk))
                cur_pred.append(hyp)
                gen_idx += 1
        _flush()

        seen: list[str] = []
        for t in ref_tokens:
            if t.speaker and t.speaker not in seen:
                seen.append(t.speaker)
        for t in pred_tokens:
            if t.speaker and t.speaker not in seen:
                seen.append(t.speaker)

        samples.append(Sample(
            file_stem=file_stem,
            example_num=int(ex.get("example", len(samples) + 1)),
            ref_tokens=tuple(ref_tokens),
            pred_tokens=tuple(pred_tokens),
            diffs=tuple(diffs),
            speakers=tuple(seen),
        ))
    return samples


def metrics_from_bewer(envelope: dict) -> dict:
    """Metrics dict from a bewer envelope (wer/cer/mtr + normalization).

    When the envelope carries ``per_speaker`` (from ``run_bewer_diarized``),
    each speaker's metrics and word counts are included.
    """
    metrics = envelope.get("metrics") or {}
    settings = envelope.get("settings") or {}
    result = {
        "wer": metrics.get("wer"),
        "cer": metrics.get("cer"),
        "mtr": metrics.get("mtr"),
        "normalization": settings.get("normalization", True),
    }
    per_speaker = envelope.get("per_speaker") or {}
    if per_speaker:
        result["per_speaker"] = {
            label: {
                "wer": sp["metrics"].get("wer"),
                "cer": sp["metrics"].get("cer"),
                "mtr": sp["metrics"].get("mtr"),
                "ref_words": sp.get("ref_words", 0),
                "gen_words": sp.get("gen_words", 0),
            }
            for label, sp in per_speaker.items()
        }
    return result


def reference_corpus(samples: list[Sample]) -> str:
    """Join the reference (ground-truth) text across all examples, one per line.

    Uses only the reference tokens — the dictated text — never the generated
    side. Used to extract medical terms from a report.
    """
    lines = []
    for s in samples:
        text = " ".join(t.text for t in s.ref_tokens).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)
