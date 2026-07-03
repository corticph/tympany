"""Shared test fixtures.

SECRET_KEY is set before importing the app (read at import time). Each test gets
a fresh TYMPANY_DATA_DIR so on-disk history/terms never leak between tests or
touch the user's real data dir.
"""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-deadbeef")

import pytest

TEST_EMAIL = "tester@corti.ai"


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TYMPANY_DATA_DIR", str(tmp_path))
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from web.app import app
    return TestClient(app)


@pytest.fixture
def auth(client):
    client.post("/login", data={"email": TEST_EMAIL})
    return client
