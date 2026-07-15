"""Console entry point for ``tympany`` — the "just run it" launcher.

Designed for zero-clone use via a GitHub-backed ``uvx`` command such as
``uvx --from git+https://github.com/corticph/tympany tympany``: it resolves
configuration (flags > environment > a local ``.env`` > interactive prompt),
starts the local web server, and opens a browser.

Credentials and analysis data stay on this machine. Corti credentials are
optional — without them the rule-based classification pass still works; they
only enable the optional LLM second pass. Persisted data (history, term lists,
generated reports) is written to a per-user OS data directory (see
``tympany.paths``), never inside the installed package — analyses may contain
PHI, so the data is local-only by design.

The older ``tympany-serve`` entry point (``web.serve:main``) is unchanged.
"""

from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from tympany.paths import persistent_secret_key

# Corti credential env vars, in the order we prompt for them. Each tuple is
# (env var, prompt label, whether the value is a secret).
_CRED_FIELDS = [
    ("CORTI_ENVIRONMENT", "Corti environment [eu]", False),
    ("CORTI_TENANT", "Corti tenant", False),
    ("CORTI_CLIENT_ID", "Corti client id", False),
    ("CORTI_CLIENT_SECRET", "Corti client secret", True),
]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tympany",
        description="Create and analyze Corti BeWER STT benchmark reports. "
        "Starts a local web app and opens it in your browser.",
    )
    p.add_argument("-p", "--port", type=int, default=int(os.environ.get("PORT", "8000")),
                   help="Port to listen on (default 8000, or $PORT).")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                   help="Bind address (default 127.0.0.1).")
    p.add_argument("--no-open", action="store_true",
                   help="Do not open the browser automatically.")
    p.add_argument("--data-dir",
                   help="Where to store history/terms/reports "
                        "(else $TYMPANY_DATA_DIR, else a per-user OS data dir).")
    p.add_argument("--dev", action="store_true",
                   help="Enable auto-reload for local development.")
    p.add_argument("--client-id", help="Corti OAuth client id (else $CORTI_CLIENT_ID).")
    p.add_argument("--client-secret", help="Corti OAuth client secret (else $CORTI_CLIENT_SECRET).")
    p.add_argument("--tenant", help="Corti tenant (else $CORTI_TENANT).")
    p.add_argument("--environment", help="Corti environment, e.g. eu (else $CORTI_ENVIRONMENT).")
    p.add_argument("--no-prompt", action="store_true",
                   help="Never prompt interactively for missing credentials.")
    return p


def _apply_flag_overrides(args: argparse.Namespace) -> None:
    """Move credential/data flags into the environment the app reads."""
    if args.data_dir:
        os.environ["TYMPANY_DATA_DIR"] = args.data_dir
    for flag, env in (
        (args.client_id, "CORTI_CLIENT_ID"),
        (args.client_secret, "CORTI_CLIENT_SECRET"),
        (args.tenant, "CORTI_TENANT"),
        (args.environment, "CORTI_ENVIRONMENT"),
    ):
        if flag:
            os.environ[env] = flag


def _prompt_for_credentials() -> None:
    """Prompt for any missing Corti credentials and offer to save them.

    Skipped entirely when not attached to a TTY (e.g. piped/CI) so the tool
    never hangs waiting on input.
    """
    missing = [f for f in _CRED_FIELDS if not os.environ.get(f[0])]
    if not missing or not sys.stdin.isatty():
        return

    print("\nCorti credentials enable the optional LLM second pass.")
    print("Leave blank to skip (rule-based analysis still works).\n")
    collected: dict[str, str] = {}
    for env, label, is_secret in missing:
        prompt = f"  {label}: "
        value = (getpass.getpass(prompt) if is_secret else input(prompt)).strip()
        if env == "CORTI_ENVIRONMENT" and not value:
            value = "eu"
        if value:
            os.environ[env] = value
            collected[env] = value

    if collected:
        _maybe_save_env(collected)


def _maybe_save_env(values: dict[str, str]) -> None:
    """Offer to append freshly entered credentials to ./.env for next time."""
    answer = input("\nSave these to ./.env for next time? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        return
    env_path = Path.cwd() / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = [f"{k}={v}" for k, v in values.items()
             if f"{k}=" not in existing]
    if not lines:
        return
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with env_path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + "\n".join(lines) + "\n")
    print(f"Saved to {env_path}")


def main() -> None:
    args = _build_parser().parse_args()

    # tympany.__init__ runs load_dotenv(), so importing the package picks up a
    # local .env. Flags then override env, and finally we prompt for the rest.
    import tympany  # noqa: F401  (triggers load_dotenv)

    _apply_flag_overrides(args)
    if not args.no_prompt:
        _prompt_for_credentials()

    # Single-user local run: use a session key that survives restarts (stored in
    # the data dir) if none was provided, so cookies work without any setup.
    if not os.environ.get("SECRET_KEY", "").strip():
        os.environ["SECRET_KEY"] = persistent_secret_key()

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print(f"\nTympany is starting at {url}")
    if not args.no_open:
        _open_when_ready(args.host, args.port, url)

    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.dev)


def _open_when_ready(host: str, port: int, url: str, timeout: float = 15.0) -> None:
    """Open the browser once the server is accepting connections.

    Runs in a daemon thread so it never blocks (or outlives) the server. Opening
    eagerly would race the bind and show a "connection refused" page.
    """
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "") else host

    def _wait() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((connect_host, port), timeout=0.5):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.25)

    threading.Thread(target=_wait, daemon=True).start()


if __name__ == "__main__":
    main()
