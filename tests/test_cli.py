"""Tests for the `tympany` launcher's argument and credential handling."""

import os

import pytest

from tympany import cli

_ENV_KEYS = [
    "PORT", "HOST", "TYMPANY_DATA_DIR",
    "CORTI_CLIENT_ID", "CORTI_CLIENT_SECRET", "CORTI_TENANT", "CORTI_ENVIRONMENT",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Clear the env vars the CLI reads/writes; monkeypatch restores them after."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_parser_defaults(clean_env):
    args = cli._build_parser().parse_args([])
    assert args.port == 8000
    assert args.host == "127.0.0.1"
    assert args.no_open is False
    assert args.dev is False


def test_port_default_reads_env(monkeypatch, clean_env):
    monkeypatch.setenv("PORT", "9001")
    args = cli._build_parser().parse_args([])
    assert args.port == 9001


def test_flag_overrides_populate_env(clean_env):
    args = cli._build_parser().parse_args([
        "--tenant", "acme",
        "--client-id", "cid",
        "--client-secret", "shh",
        "--environment", "us",
        "--data-dir", "/tmp/tympany-x",
    ])
    cli._apply_flag_overrides(args)
    assert os.environ["CORTI_TENANT"] == "acme"
    assert os.environ["CORTI_CLIENT_ID"] == "cid"
    assert os.environ["CORTI_CLIENT_SECRET"] == "shh"
    assert os.environ["CORTI_ENVIRONMENT"] == "us"
    assert os.environ["TYMPANY_DATA_DIR"] == "/tmp/tympany-x"


def test_prompt_skipped_without_tty(monkeypatch, clean_env):
    class _FakeStdin:
        def isatty(self):
            return False

    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(
        "builtins.input",
        lambda *a, **k: pytest.fail("should not prompt without a TTY"),
    )
    cli._prompt_for_credentials()  # returns without prompting
