"""GET /api/instagram/status: set up or not without network; with check=true, instagram-check on fakes."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from app.main import create_app  # noqa: E402
from test_instagram_real import KEY_ID, SECRET, TOKEN, meta, settings  # noqa: E402, F401  (pytest fixtures)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, stability_interval_s=0)) as test_client:
        yield test_client


def test_without_check_only_the_setup_is_reported(client, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    body = client.get("/api/instagram/status").json()
    assert body["configured"] is False and body["checked"] is False and body["token_type"] == "System User, no expiry"


def test_check_reports_the_account_quota_and_media_host_and_no_secret(client, meta):
    response = client.get("/api/instagram/status", params={"check": "true"})
    body = response.json()
    assert (body["configured"], body["checked"], body["ok"], body["username"]) == (True, True, True, "autoposter.test")
    assert body["quota"] == {"used": 3, "total": 50} and body["media_host_ok"] is True and body["problems"] == []
    assert all(method == "GET" for method, _ in meta.calls)  # Never a container, never media_publish.
    for secret in (TOKEN, SECRET, KEY_ID, "X-Amz-Signature"):
        assert secret not in response.text
