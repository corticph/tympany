"""Tympany's diff model and BeWER-envelope mapping.

``Token`` / ``DiffGroup`` / ``Sample`` are the shapes the categorizer consumes.
They are built from a bewer evaluation envelope (see ``tympany.bewer_eval``) via
``from_bewer`` — Tympany no longer parses any report HTML.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    cls: str   # "ok" | "sub" | "del" | "ins"
    text: str


@dataclass(frozen=True)
class DiffGroup:
    ref: tuple[str, ...]
    pred: tuple[str, ...]

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


def samples_to_payload(samples: list[Sample]) -> list[dict]:
    """Serialise parsed samples into the JSON-friendly form persisted in history.

    Only the full per-example token streams (with their class) are kept — that
    is everything the re-run needs to reconstruct reference text and a
    corrected generated text with excluded errors removed.
    """
    return [
        {
            "example": s.example_num,
            "ref_tokens": [{"cls": t.cls, "text": t.text} for t in s.ref_tokens],
            "pred_tokens": [{"cls": t.cls, "text": t.text} for t in s.pred_tokens],
        }
        for s in samples
    ]


def from_bewer(envelope: dict, file_stem: str = "report") -> list[Sample]:
    """Map a bewer JSON envelope (see tympany.bewer_eval) into Samples.

    Each alignment op becomes ref/pred Tokens (MATCH→ok, SUBSTITUTE→sub,
    DELETE→del, INSERT→ins); contiguous non-match ops are grouped into
    DiffGroups, exactly the shape the categorizer consumes. This is the
    This is how Tympany turns a bewer evaluation into reviewable diffs.
    """
    samples: list[Sample] = []
    for ex in envelope.get("examples", []):
        ref_tokens: list[Token] = []
        pred_tokens: list[Token] = []
        diffs: list[DiffGroup] = []
        cur_ref: list[str] = []
        cur_pred: list[str] = []

        def _flush() -> None:
            if cur_ref or cur_pred:
                diffs.append(DiffGroup(tuple(cur_ref), tuple(cur_pred)))
                cur_ref.clear()
                cur_pred.clear()

        for op in ex.get("ops", []):
            op_type = (op.get("type") or "").upper()
            ref, hyp = op.get("ref"), op.get("hyp")
            if op_type == "MATCH":
                _flush()
                ref_tokens.append(Token("ok", ref))
                pred_tokens.append(Token("ok", hyp))
            elif op_type == "SUBSTITUTE":
                ref_tokens.append(Token("sub", ref))
                pred_tokens.append(Token("sub", hyp))
                cur_ref.append(ref)
                cur_pred.append(hyp)
            elif op_type == "DELETE":
                ref_tokens.append(Token("del", ref))
                cur_ref.append(ref)
            elif op_type == "INSERT":
                pred_tokens.append(Token("ins", hyp))
                cur_pred.append(hyp)
        _flush()

        samples.append(Sample(
            file_stem=file_stem,
            example_num=int(ex.get("example", len(samples) + 1)),
            ref_tokens=tuple(ref_tokens),
            pred_tokens=tuple(pred_tokens),
            diffs=tuple(diffs),
        ))
    return samples


def metrics_from_bewer(envelope: dict) -> dict:
    """Metrics dict from a bewer envelope (wer/cer/mtr + normalization)."""
    metrics = envelope.get("metrics") or {}
    settings = envelope.get("settings") or {}
    return {
        "wer": metrics.get("wer"),
        "cer": metrics.get("cer"),
        "mtr": metrics.get("mtr"),
        "normalization": settings.get("normalization", True),
    }


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
