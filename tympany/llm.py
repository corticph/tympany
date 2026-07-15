"""LLM-assisted classification for edits the rule chain couldn't resolve.

Only edits classified as 'misrecognition' or 'context_dependent' by the
rule chain are sent to the LLM. All others are returned as-is.

The LLM is the Corti Agentic Framework: a dedicated classifier agent is
invoked via Corti (see tympany/corti.py). Provider selection is simply:
  - Corti credentials configured → 'corti'
  - otherwise                     → None (skip LLM, return rule-chain result)

The agent returns a structured JSON object:
  {
    "category": "<category string>",
    "classification": "<formatting_error|replacement_candidate|context_dependent|misrecognition>",
    "risk_level": "<low|medium|high>",
    "replacement_candidate": <true|false>,
    "reasoning": "<one sentence>"
  }
"""

from __future__ import annotations

import concurrent.futures
import json
import sys
import threading
from typing import MutableMapping, Optional

from . import corti
from .categorize import Edit


# Max concurrent message:send round-trips to the Corti agent. Each uncertain
# edit is an independent, isolated request (no shared context), so they can run
# in parallel; cap the fan-out to stay friendly to the API.
_MAX_WORKERS = 8


# ---------------------------------------------------------------------------
# Categories the LLM is allowed to assign
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = {
    "number_format", "date_format", "year_format",
    "abbreviation_expansion", "roman_numeral", "ordinal_format", "formatting_marker",
    "latin_greek_spelling", "spelling_close", "compound_split", "compound_merge",
    "misrecognition", "medication_or_device", "pure_insertion", "pure_deletion",
}

_VALID_CLASSIFICATIONS = {
    "formatting_error", "replacement_candidate", "context_dependent", "misrecognition",
}

_VALID_RISKS = {"low", "medium", "high"}

# Only upgrade edits that the rule chain flagged as uncertain
_ELIGIBLE_CLASSIFICATIONS = {"misrecognition", "context_dependent"}


# ---------------------------------------------------------------------------
# Prompt — the classification rules live in the Corti agent's systemPrompt
# (tympany.corti.SYSTEM_PROMPT); each message carries only the ref/gen pair.
# ---------------------------------------------------------------------------

def _build_user_message(edit: Edit) -> str:
    ref_str = " ".join(edit.ref) if edit.ref else "(nothing)"
    gen_str = " ".join(edit.pred) if edit.pred else "(nothing)"
    return f'Reference: "{ref_str}"\nGenerated: "{gen_str}"'


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(raw: str, original: Edit) -> Edit:
    """Parse LLM JSON response and return an updated Edit, falling back to
    the original if the response is malformed or contains invalid values."""
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return original

    category = data.get("category", "")
    classification = data.get("classification", "")
    risk_level = data.get("risk_level", "")
    is_candidate = bool(data.get("replacement_candidate", False))
    reasoning = str(data.get("reasoning", ""))

    if category not in _VALID_CATEGORIES:
        category = original.category
    if classification not in _VALID_CLASSIFICATIONS:
        classification = original.classification
    if risk_level not in _VALID_RISKS:
        risk_level = original.risk_level

    detail = f"[LLM] {reasoning}" if reasoning else "[LLM]"
    return _EditWithLLM(
        ref=original.ref,
        pred=original.pred,
        category=category,
        detail=detail,
        _classification=classification,
        _risk_level=risk_level,
        _is_replacement_candidate=is_candidate,
    )


# ---------------------------------------------------------------------------
# Extended Edit subclass that stores LLM-overridden fields
# ---------------------------------------------------------------------------

class _EditWithLLM(Edit):
    """Edit whose classification/risk/candidate fields come from LLM output."""

    def __init__(
        self,
        *,
        ref: tuple[str, ...],
        pred: tuple[str, ...],
        category: str,
        detail: str,
        _classification: str,
        _risk_level: str,
        _is_replacement_candidate: bool,
    ) -> None:
        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "pred", pred)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "detail", detail)
        object.__setattr__(self, "_classification", _classification)
        object.__setattr__(self, "_risk_level", _risk_level)
        object.__setattr__(self, "_is_replacement_candidate", _is_replacement_candidate)

    @property
    def classification(self) -> str:
        return self._classification

    @property
    def risk_level(self) -> str:
        return self._risk_level

    @property
    def is_replacement_candidate(self) -> bool:
        return self._is_replacement_candidate


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _call_corti(
    edits: list[Edit],
    outcome: Optional[MutableMapping] = None,
) -> list[Edit]:
    eligible = [(i, e) for i, e in enumerate(edits) if e.classification in _ELIGIBLE_CLASSIFICATIONS]
    if not eligible:
        return edits

    try:
        agent_id = corti.ensure_agent()
    except Exception as exc:
        # Whole pass failed before any edit could be classified — every eligible
        # edit silently falls back to its rule-chain result. Record that so the
        # caller can surface a degradation notice instead of presenting the
        # rule-based output as if the LLM had run.
        print(f"  Corti agent setup error: {exc}", file=sys.stderr)
        if outcome is not None:
            outcome["eligible"] = outcome.get("eligible", 0) + len(eligible)
            outcome["failed"] = outcome.get("failed", 0) + len(eligible)
            outcome["setup_failed"] = True
        return edits

    # Eligible edits are independent message:send round-trips, so run them
    # concurrently over a thread pool (httpx.post is blocking I/O). Non-eligible
    # edits pass through untouched; results are written back by index to
    # preserve the original order.
    results: list[Edit] = list(edits)

    # Shared, lock-guarded agent id so that a server-side deletion triggers
    # exactly one recreation across all worker threads rather than one per edit.
    # `failed` counts edits that fell back to their rule-chain result.
    state = {"agent_id": agent_id, "failed": 0}
    lock = threading.Lock()

    def _recover_agent(stale_id: str) -> str:
        with lock:
            # Another thread may already have recreated the agent.
            if state["agent_id"] != stale_id:
                return state["agent_id"]
            corti.invalidate_agent()
            new_id = corti.ensure_agent()
            state["agent_id"] = new_id
            return new_id

    def _classify(edit: Edit) -> Edit:
        current = state["agent_id"]
        try:
            try:
                raw = corti.classify_pair(_build_user_message(edit), current)
            except corti.AgentNotFoundError:
                # Cached agent was deleted on the server — recreate once and retry.
                current = _recover_agent(current)
                raw = corti.classify_pair(_build_user_message(edit), current)
            return _parse_response(raw, edit)
        except Exception as exc:
            print(f"  Corti API error: {exc}", file=sys.stderr)
            with lock:
                state["failed"] += 1
            return edit

    workers = min(_MAX_WORKERS, len(eligible))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {pool.submit(_classify, edit): i for i, edit in eligible}
        for future in concurrent.futures.as_completed(future_to_index):
            results[future_to_index[future]] = future.result()

    if outcome is not None:
        outcome["eligible"] = outcome.get("eligible", 0) + len(eligible)
        outcome["failed"] = outcome.get("failed", 0) + state["failed"]

    return results


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def detect_provider(preferred: Optional[str] = None) -> Optional[str]:
    """Return 'corti' if Corti credentials are configured, else None.

    `preferred` is accepted for backward compatibility (callers may pass
    'corti' or 'auto'); the result depends only on configured credentials.
    """
    return "corti" if corti.is_configured() else None


def classify_with_llm(
    edits: list[Edit],
    provider: str,
    outcome: Optional[MutableMapping] = None,
) -> list[Edit]:
    """Run LLM classification on eligible edits. Returns updated list.

    When ``outcome`` is provided, the LLM pass records its result into it
    (accumulating across calls): ``eligible`` edits attempted, how many
    ``failed`` and fell back to the rule-chain result, and ``setup_failed`` if
    the agent could not be reached at all. Callers use this to tell a genuine
    LLM result apart from a silent rule-based fallback.
    """
    eligible = [e for e in edits if e.classification in _ELIGIBLE_CLASSIFICATIONS]
    if not eligible:
        return edits

    if provider == "corti":
        return _call_corti(edits, outcome)

    return edits
