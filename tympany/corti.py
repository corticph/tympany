"""Corti Agentic Framework client for the LLM second pass.

Replaces the previous direct Anthropic/OpenAI integration. Authentication uses
OAuth2 client credentials (server-to-server); the short-lived bearer token is
cached and refreshed automatically.

Required environment variables:
    CORTI_CLIENT_ID      — OAuth2 client id (from the Corti Console)
    CORTI_CLIENT_SECRET  — OAuth2 client secret
    CORTI_TENANT         — tenant name
    CORTI_ENVIRONMENT    — "eu" or "us"

The classifier agent is created automatically on first use and cached to
{TYMPANY_DATA_DIR}/corti_agent.json, so it is reused on subsequent runs.

API shape (https://docs.corti.ai). Note the agentic endpoints live under
`/agents` (NOT `/v2/agents` — that path 403s; the standard REST API uses /v2):
    token   POST https://auth.{env}.corti.app/realms/{tenant}/protocol/openid-connect/token
    create  POST https://api.{env}.corti.app/agents
    run     POST https://api.{env}.corti.app/agents/{id}/v1/message:send
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

from tympany.paths import data_dir

# Refresh the token this many seconds before it actually expires.
_TOKEN_MARGIN = 30
# How long to wait on any single Corti HTTP call.
_HTTP_TIMEOUT = 60.0

# Definition of the auto-created classifier agent. The systemPrompt is baked into
# the agent at creation; each message then carries only the ref/gen pair.
_AGENT_NAME = "Tympany STT Error Classifier"
_AGENT_DESCRIPTION = (
    "Classifies differences between reference and speech-recognition-generated "
    "medical text into error categories and clinical risk levels for the "
    "Tympany BeWER analysis tool."
)
SYSTEM_PROMPT = """\
You are a medical transcription quality reviewer. You will be given a pair of \
text tokens: the reference text (what was dictated) and the generated text \
(what the speech recognition system produced). Your task is to classify the \
difference between them.

Respond ONLY with a JSON object — no extra text, no markdown fences.

Classification rules:
- "formatting_error": difference is purely numeric or date formatting \
(e.g. "3" vs "three", "04/19" vs "April 19").
- "replacement_candidate": difference is a known abbreviation, \
acronym, roman numeral, or verbalized command that could be fixed by adding a \
replacement rule (e.g. "bp" vs "blood pressure", "tia" vs "transient ischemic attack", \
"nkda" vs "no known drug allergies").
- "context_dependent": difference is a close spelling variant, \
compound boundary, or Latin/Greek alternate spelling where clinical meaning is \
probably preserved but human review is warranted.
- "misrecognition": the speech recognition produced something \
clinically different or meaningless — a real error that changes meaning.

Pay special attention to medical entities — medications, dosages, devices, \
conditions, procedures, anatomy, and lab values. When either side of the pair \
is such an entity and the other side changes its identity or value (e.g. a \
different drug, a wrong dose, a different condition), classify it as category \
"medication_or_device" with classification "misrecognition", since these \
errors are the most clinically dangerous. Only treat a medical-entity \
difference as lower risk when it is clearly a benign formatting, abbreviation, \
or spelling variant of the SAME entity.

Return this exact JSON structure:
{
  "category": "<one of the category values>",
  "classification": "<formatting_error|replacement_candidate|context_dependent|misrecognition>",
  "reasoning": "<one sentence>"
}

Valid category values: number_format, date_format, year_format, \
abbreviation_expansion, roman_numeral, ordinal_format, formatting_marker, \
latin_greek_spelling, spelling_close, compound_split, compound_merge, \
misrecognition, medication_or_device, pure_insertion, pure_deletion.
"""

# Definition of the medical-term extractor agent. Its message carries only the
# reference text; this prompt (constrained for speed/cost) does the work.
_TERM_AGENT_NAME = "Tympany Medical Term Extractor"
_TERM_AGENT_DESCRIPTION = (
    "Extracts distinct medical terms from clinical reference text for the "
    "Tympany Medical Term Recall workflow."
)
TERM_EXTRACTION_PROMPT = """\
You extract medical terms from clinical reference text.

Output ONLY the distinct medical terms that appear verbatim in the input — \
conditions, medications, devices, procedures, anatomy, lab tests, and \
clinically significant abbreviations. One term per line. No numbering, no \
commentary, no duplicates. Preserve the source casing. If there are none, \
output nothing.
"""

# Agents this app provisions, keyed by a stable kind. The systemPrompt is baked
# into each agent at creation; messages then carry only the per-call payload.
_AGENT_SPECS: dict[str, dict[str, str]] = {
    "classifier": {
        "name": _AGENT_NAME,
        "description": _AGENT_DESCRIPTION,
        "system_prompt": SYSTEM_PROMPT,
    },
    "term_extractor": {
        "name": _TERM_AGENT_NAME,
        "description": _TERM_AGENT_DESCRIPTION,
        "system_prompt": TERM_EXTRACTION_PROMPT,
    },
}

# Module-level caches (process lifetime).
_token_cache: dict[str, object] = {"access_token": None, "expires_at": 0.0}
_token_lock = threading.Lock()
_agent_id_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _env() -> str:
    return os.environ.get("CORTI_ENVIRONMENT", "").strip().lower()


def _tenant() -> str:
    return os.environ.get("CORTI_TENANT", "").strip()


def is_configured() -> bool:
    """True when all required Corti credentials are present."""
    return all(
        os.environ.get(key)
        for key in ("CORTI_CLIENT_ID", "CORTI_CLIENT_SECRET", "CORTI_TENANT", "CORTI_ENVIRONMENT")
    )


def _auth_url() -> str:
    return f"https://auth.{_env()}.corti.app/realms/{_tenant()}/protocol/openid-connect/token"


def _api_base() -> str:
    return f"https://api.{_env()}.corti.app"


def _api_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Tenant-Name": _tenant(),
        "Content-Type": "application/json",
    }


class AgentNotFoundError(RuntimeError):
    """Raised when message:send reports the agent id no longer exists."""


def _raise_for_status(resp: httpx.Response, what: str) -> None:
    """raise_for_status with a clearer hint for the common 403 case."""
    if resp.status_code == 403:
        raise RuntimeError(
            f"Corti returned 403 Forbidden for {what}. The credentials authenticate "
            "successfully but are not authorized for the Agentic Framework. Confirm "
            "the Agentic Framework is enabled for this project/region and that the API "
            "client has agent access (Corti Console / help.corti.app)."
        )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------

def _get_token() -> str:
    """Return a valid access token, fetching/refreshing as needed.

    Double-checked locking keeps concurrent callers (the parallel second pass)
    from each kicking off their own token fetch: the first thread refreshes
    under the lock, the rest find the fresh token and reuse it.
    """
    now = time.time()
    if _token_cache["access_token"] and now < float(_token_cache["expires_at"]):
        return str(_token_cache["access_token"])

    with _token_lock:
        # Re-check inside the lock — another thread may have just refreshed.
        now = time.time()
        if _token_cache["access_token"] and now < float(_token_cache["expires_at"]):
            return str(_token_cache["access_token"])

        resp = httpx.post(
            _auth_url(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": os.environ.get("CORTI_CLIENT_ID", ""),
                "client_secret": os.environ.get("CORTI_CLIENT_SECRET", ""),
                "grant_type": "client_credentials",
                "scope": "openid",
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 300))
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + max(expires_in - _TOKEN_MARGIN, 0)
        return token


# ---------------------------------------------------------------------------
# Agent provisioning
# ---------------------------------------------------------------------------

def _agent_cache_file() -> Path:
    return data_dir() / "corti_agent.json"


def _load_persisted_agents() -> dict[str, str]:
    """Return the persisted {kind: agent_id} map for the current tenant/env.

    Accepts the legacy single-id format ({"agent_id": ...}) and maps it to the
    classifier so older caches keep working.
    """
    path = _agent_cache_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("tenant") != _tenant() or data.get("environment") != _env():
        return {}
    agents = data.get("agents")
    if isinstance(agents, dict):
        return {k: v for k, v in agents.items() if v}
    legacy = data.get("agent_id")  # back-compat with the pre-multi-agent format
    return {"classifier": legacy} if legacy else {}


def _persist_agents(agents: dict[str, str]) -> None:
    path = _agent_cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tenant": _tenant(), "environment": _env(), "agents": agents}),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  Corti: could not persist agent ids: {exc}", file=sys.stderr)


def invalidate_agent(key: str = "classifier") -> None:
    """Forget one cached agent id (process + persisted), so the next
    ensure_agent(key) creates a fresh one. Used to recover when an agent was
    deleted on the Corti side."""
    _agent_id_cache.pop(key, None)
    persisted = _load_persisted_agents()
    if persisted.pop(key, None) is not None:
        _persist_agents(persisted)


def ensure_agent(key: str = "classifier") -> str:
    """Return the agent id for ``key`` (classifier | term_extractor), creating
    it if necessary. Resolution order: process cache → persisted file → create.
    """
    if _agent_id_cache.get(key):
        return _agent_id_cache[key]

    persisted = _load_persisted_agents()
    if persisted.get(key):
        _agent_id_cache[key] = persisted[key]
        return persisted[key]

    spec = _AGENT_SPECS[key]
    resp = httpx.post(
        f"{_api_base()}/agents",
        headers=_api_headers(),
        json={
            "name": spec["name"],
            "description": spec["description"],
            "systemPrompt": spec["system_prompt"],
            "experts": [],
        },
        timeout=_HTTP_TIMEOUT,
    )
    _raise_for_status(resp, "POST /agents (create agent)")
    agent_id = resp.json()["id"]
    _agent_id_cache[key] = agent_id
    _persist_agents({**persisted, key: agent_id})
    print(f"  Corti: created {spec['name']} agent {agent_id}", file=sys.stderr)
    return agent_id


# ---------------------------------------------------------------------------
# Running the agent
# ---------------------------------------------------------------------------

def _extract_text(task_response: dict) -> str:
    """Pull the agent's text out of a message:send response.

    Primary location is task.status.message.parts[].text; fall back to the
    parts of any returned artifacts.
    """
    task = task_response.get("task", task_response)

    message = (task.get("status") or {}).get("message") or {}
    for part in message.get("parts", []):
        if part.get("text"):
            return str(part["text"])

    for artifact in task.get("artifacts", []) or []:
        for part in artifact.get("parts", []):
            if part.get("text"):
                return str(part["text"])

    return ""


def send_message(prompt: str, agent_id: str) -> str:
    """Send one message to an agent and return its text response.

    No contextId is sent, so each call is isolated (no memory bleed). The agent's
    behaviour lives entirely in its systemPrompt (set at creation), so the
    message carries only ``prompt``.
    """
    resp = httpx.post(
        f"{_api_base()}/agents/{agent_id}/v1/message:send",
        headers=_api_headers(),
        json={
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": prompt}],
                "messageId": str(uuid.uuid4()),
                "kind": "message",
            }
        },
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code == 404 and "agent_not_found" in resp.text:
        raise AgentNotFoundError(f"Agent {agent_id} not found (deleted server-side?)")
    _raise_for_status(resp, "message:send")
    return _extract_text(resp.json())


def classify_pair(prompt: str, agent_id: str) -> str:
    """Send one classification request (ref/gen pair) to the classifier agent."""
    return send_message(prompt, agent_id)


def extract_terms(reference_text: str) -> list[str]:
    """Extract distinct medical terms from reference text via the extractor agent.

    Returns a de-duplicated list (case-insensitive), preserving first-seen order
    and source casing. One round-trip; recovers once if the agent was deleted.
    """
    if not reference_text.strip():
        return []
    agent_id = ensure_agent("term_extractor")
    try:
        raw = send_message(reference_text, agent_id)
    except AgentNotFoundError:
        invalidate_agent("term_extractor")
        raw = send_message(reference_text, ensure_agent("term_extractor"))

    terms: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        term = line.strip().lstrip("-•*").strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms
