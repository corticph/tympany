"""Resolve where Tympany persists per-user data (history, terms, generated
reports, the cached Corti agent map).

Resolution order:
    1. $TYMPANY_DATA_DIR if set (explicit override; used by deployments).
    2. A per-user OS data directory (e.g. ~/.local/share/tympany on Linux,
       ~/Library/Application Support/tympany on macOS).

The per-user default — rather than a directory inside the installed package —
matters for two reasons:
    * When run ephemerally via ``uvx`` from GitHub/source, the package lives in
      a throwaway, possibly read-only location, so it is not a viable place to
      write.
    * Analyses may contain PHI. Persisted data must stay local to the user's
      machine and must never be bundled into the distributed package.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from platformdirs import user_data_dir

_APP_NAME = "tympany"


def data_dir() -> Path:
    """Return the base directory for Tympany's persisted data."""
    override = os.environ.get("TYMPANY_DATA_DIR")
    if override:
        return Path(override)
    return Path(user_data_dir(_APP_NAME, appauthor=False))


def persistent_secret_key() -> str:
    """Return a session-signing key that survives restarts.

    Reads (or creates) ``<data_dir>/.secret_key`` so local single-user runs keep
    their logins across restarts without the user having to set ``SECRET_KEY``.
    Falls back to an ephemeral key if the file can't be written (e.g. read-only
    install dir).
    """
    key_file = data_dir() / ".secret_key"
    try:
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    key = secrets.token_urlsafe(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, encoding="utf-8")
        key_file.chmod(0o600)
    except OSError:
        pass  # ephemeral for this run; sessions just won't survive a restart
    return key
