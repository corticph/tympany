"""Saved medical-term lists, per user, for BeWER's Medical Term Recall metric.

Layout:
    <DATA_DIR>/terms/<user_key>/<name>.txt   (one term per line)

A term list is just a newline-delimited text file. It is keyed by a sanitised
name (which is also its display name). These lists are reusable across reports
so a user does not have to re-paste the same vocabulary each time.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tympany.paths import data_dir
from web.history import user_key  # reuse the stable per-user directory naming


def _data_dir() -> Path:
    return data_dir()


def _terms_dir(email: str) -> Path:
    return _data_dir() / "terms" / user_key(email)


def safe_name(name: str) -> str:
    """Sanitise a list name into a filesystem-safe key (empty if unusable)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-_.")
    return stem[:80]


def normalize_terms(text: str) -> str:
    """Keep one non-empty, trimmed term per line."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines) + ("\n" if lines else "")


def term_count(text: str) -> int:
    return len([ln for ln in (text or "").splitlines() if ln.strip()])


def terms_path(email: str, name: str) -> Optional[Path]:
    safe = safe_name(name)
    if not safe:
        return None
    return _terms_dir(email) / f"{safe}.txt"


def save_terms(email: str, name: str, content: str) -> Optional[str]:
    """Persist a term list (overwrites a list of the same name). Returns its key."""
    path = terms_path(email, name)
    content = normalize_terms(content)
    if path is None or not content.strip():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return safe_name(name)


def read_terms(email: str, name: str) -> Optional[str]:
    path = terms_path(email, name)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def exists(email: str, name: str) -> bool:
    path = terms_path(email, name)
    return path is not None and path.is_file()


def duplicate_terms(email: str, name: str) -> Optional[str]:
    """Copy a saved list to a new, non-colliding name (e.g. '<name>-copy')."""
    content = read_terms(email, name)
    if content is None:
        return None
    base = f"{safe_name(name)}-copy"
    candidate, i = base, 2
    while exists(email, candidate):
        candidate, i = f"{base}-{i}", i + 1
    return save_terms(email, candidate, content)


def list_terms(email: str) -> list[dict]:
    """Saved term lists for this user (name, term count, created/updated time)."""
    dir_ = _terms_dir(email)
    if not dir_.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(dir_.glob("*.txt")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        items.append({
            "name": path.stem,
            "count": term_count(content),
            "created_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).replace(microsecond=0).isoformat(),
        })
    return items


def delete_terms(email: str, name: str) -> bool:
    path = terms_path(email, name)
    if path is None or not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False
