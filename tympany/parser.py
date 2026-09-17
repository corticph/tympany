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
    left_compound: bool = False   # token is the left part of a split compound
    right_compound: bool = False  # token is the right part of a split compound

    @property
    def is_compound_partial(self) -> bool:
        return self.left_compound or self.right_compound


@dataclass(frozen=True)
class DiffGroup:
    ref: tuple[str, ...]
    pred: tuple[str, ...]
    # True when every op in this group is a compound-boundary diff — the
    # ref and pred text is identical and only the compound markers differ.
    compound_only: bool = False

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
            "ref_tokens": [
                {"cls": t.cls, "text": t.text, "left_compound": t.left_compound, "right_compound": t.right_compound}
                for t in s.ref_tokens
            ],
            "pred_tokens": [
                {"cls": t.cls, "text": t.text, "left_compound": t.left_compound, "right_compound": t.right_compound}
                for t in s.pred_tokens
            ],
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
        # Track whether every op in the current diff group is a compound-only
        # boundary difference (ref text == hyp text, only compound markers).
        cur_ref_ops: list[dict] = []
        cur_pred_ops: list[dict] = []

        def _flush() -> None:
            if cur_ref or cur_pred:
                # compound_only: every paired (ref, pred) token has identical
                # text and differs only in compound markers. Only applies when
                # there are tokens on both sides (substitute groups), not to
                # insert-only or delete-only groups.
                paired = [
                    (r, p) for r, p in zip(cur_ref_ops, cur_pred_ops)
                    if r.get("text") is not None and p.get("text") is not None
                ]
                compound_only = (
                    bool(paired)
                    and all(r["text"] == p["text"] for r, p in paired)
                    and any(
                        r.get("left_compound") or r.get("right_compound")
                        or p.get("left_compound") or p.get("right_compound")
                        for r, p in paired
                    )
                )
                diffs.append(DiffGroup(tuple(cur_ref), tuple(cur_pred), compound_only=compound_only))
                cur_ref.clear()
                cur_pred.clear()
                cur_ref_ops.clear()
                cur_pred_ops.clear()

        for op in ex.get("ops", []):
            op_type = (op.get("type") or "").upper()
            ref, hyp = op.get("ref"), op.get("hyp")
            hyp_left = bool(op.get("hyp_left_partial", False))
            hyp_right = bool(op.get("hyp_right_partial", False))
            ref_left = bool(op.get("ref_left_partial", False))
            ref_right = bool(op.get("ref_right_partial", False))
            if op_type == "MATCH":
                _flush()
                ref_tokens.append(Token("ok", ref, left_compound=ref_left, right_compound=ref_right))
                pred_tokens.append(Token("ok", hyp, left_compound=hyp_left, right_compound=hyp_right))
            elif op_type == "SUBSTITUTE":
                ref_tokens.append(Token("sub", ref, left_compound=ref_left, right_compound=ref_right))
                pred_tokens.append(Token("sub", hyp, left_compound=hyp_left, right_compound=hyp_right))
                cur_ref.append(ref)
                cur_pred.append(hyp)
                cur_ref_ops.append({"text": ref, "left_compound": ref_left, "right_compound": ref_right})
                cur_pred_ops.append({"text": hyp, "left_compound": hyp_left, "right_compound": hyp_right})
            elif op_type == "DELETE":
                ref_tokens.append(Token("del", ref, left_compound=ref_left, right_compound=ref_right))
                cur_ref.append(ref)
                cur_ref_ops.append({"text": ref, "left_compound": ref_left, "right_compound": ref_right})
            elif op_type == "INSERT":
                pred_tokens.append(Token("ins", hyp, left_compound=hyp_left, right_compound=hyp_right))
                cur_pred.append(hyp)
                cur_pred_ops.append({"text": hyp, "left_compound": hyp_left, "right_compound": hyp_right})
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
