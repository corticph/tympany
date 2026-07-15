"""Tests for data-dir resolution and the persistent session key."""

from tympany import paths


def test_data_dir_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TYMPANY_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path


def test_data_dir_default_is_per_user_dir(monkeypatch):
    monkeypatch.delenv("TYMPANY_DATA_DIR", raising=False)
    d = paths.data_dir()
    assert d.is_absolute()
    assert d.name == "tympany"
    # Must not resolve to a directory inside the installed package.
    assert "site-packages" not in str(d)


def test_persistent_secret_key_is_stable(monkeypatch, tmp_path):
    monkeypatch.setenv("TYMPANY_DATA_DIR", str(tmp_path))
    first = paths.persistent_secret_key()
    second = paths.persistent_secret_key()
    assert first and first == second
    assert (tmp_path / ".secret_key").read_text(encoding="utf-8").strip() == first


def test_persistent_secret_key_falls_back_when_unwritable(monkeypatch, tmp_path):
    # Point at a path whose parent is a file, so the dir can't be created.
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TYMPANY_DATA_DIR", str(blocker / "nested"))
    key = paths.persistent_secret_key()
    assert key  # ephemeral key returned rather than raising
