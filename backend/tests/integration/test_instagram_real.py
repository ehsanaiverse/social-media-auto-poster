"""R1 (D48): real Instagram through graph.facebook.com and the S3-compatible media host, on fakes only.

``operations/instagram.py: _transport`` is replaced by one MockTransport that plays both Meta's Graph API and
the bucket, so no test can reach a network. The token, the bucket's keys and the signatures must never appear
in any output, log, report or database.
"""

from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from social_media_auto_poster import main as cli
from social_media_auto_poster.config import ensure_data_dirs, load_settings
from social_media_auto_poster.models import JobStatus, Platform
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
TOKEN = "EAA-SYSTEM-USER-TOKEN-must-never-leak-51f0"
SECRET = "B2-SECRET-KEY-must-never-leak-77aa"
KEY_ID = "004b2keyid0000000000000001"
USER_ID = "17841400000000001"
CONTAINER_ID = "17889455560051444"
MEDIA_ID = "17920238422030506"
ENDPOINT_HOST = "s3.us-west-004.backblazeb2.com"


class FakeMeta:
    """Meta's Graph API and a B2 bucket. Creating a container fetches the picture by its presigned URL, as Meta does."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == ENDPOINT_HOST:
            return self._bucket(request)
        assert request.url.host == "graph.facebook.com", request.url
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path = request.url.path.removeprefix("/v27.0")
        self.calls.append((request.method, path))
        if request.method == "GET" and path == f"/{USER_ID}":
            return httpx.Response(200, json={"id": USER_ID, "username": "autoposter.test"})
        if path == f"/{USER_ID}/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_usage": 3, "config": {"quota_total": 50}}]})
        if request.method == "POST" and path == f"/{USER_ID}/media":
            fetched = self._bucket(httpx.Request("GET", httpx.URL(__import__("json").loads(request.content)["image_url"])))
            assert fetched.status_code == 200 and fetched.content == JPEG
            return httpx.Response(200, json={"id": CONTAINER_ID})
        if path == f"/{CONTAINER_ID}":
            return httpx.Response(200, json={"status_code": "FINISHED", "id": CONTAINER_ID})
        if request.method == "POST" and path == f"/{USER_ID}/media_publish":
            return httpx.Response(200, json={"id": MEDIA_ID})
        return httpx.Response(404)

    def _bucket(self, request: httpx.Request) -> httpx.Response:
        key = request.url.path.removeprefix("/autoposter-media/")
        query = dict(parse_qsl(request.url.query.decode()))
        if request.method == "PUT":
            assert "authorization" in request.headers
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        if request.method == "GET" and query.get("X-Amz-Expires") == "3600" and "X-Amz-Signature" in query:
            assert "authorization" not in request.headers  # A presigned URL carries its own signature only.
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        return httpx.Response(403)


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(instagram_ops, "_transport", fake.transport)
    for name, value in {
        "INSTAGRAM_ACCESS_TOKEN": TOKEN, "INSTAGRAM_USER_ID": USER_ID,
        "MEDIA_S3_ENDPOINT": f"https://{ENDPOINT_HOST}", "MEDIA_S3_REGION": "us-west-004",
        "MEDIA_S3_ACCESS_KEY_ID": KEY_ID, "MEDIA_S3_SECRET_ACCESS_KEY": SECRET, "MEDIA_S3_BUCKET": "autoposter-media",
    }.items():
        monkeypatch.setenv(name, value)
    return fake


@pytest.fixture
def settings():
    settings = load_settings()
    ensure_data_dirs(settings)
    return settings


def _no_secret_anywhere(settings, captured) -> None:
    texts = [captured.out, captured.err]
    texts += [p.read_text(encoding="utf-8", errors="replace") for p in settings.base_dir.rglob("*") if p.is_file()
              and p.suffix in (".log", ".txt", "")]
    texts += [p.read_bytes().decode("latin-1") for p in settings.base_dir.rglob("*.db")]
    for text in texts:
        assert TOKEN not in text and SECRET not in text and KEY_ID not in text and "X-Amz-Signature" not in text


def test_instagram_check_reads_the_account_and_quota_and_round_trips_the_media_host(settings, meta, capsys):
    assert cli.main(["instagram-check"]) == 0

    captured = capsys.readouterr()
    assert "account autoposter.test (ID 17841400000000001)" in captured.out
    assert "publishing quota: 3 of 50 posts used" in captured.out
    assert "media host: upload, fetch by presigned URL, and delete worked" in captured.out
    assert all(method == "GET" for method, _ in meta.calls)  # Never a container, never media_publish.
    assert meta.objects == {}
    _no_secret_anywhere(settings, captured)


def test_instagram_check_without_credentials_says_what_is_missing(settings, monkeypatch, capsys):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    assert cli.main(["instagram-check"]) == 1
    assert "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set" in capsys.readouterr().out


def test_a_real_instagram_run_publishes_once_and_cleans_up(settings, meta, capsys):
    incoming = settings.incoming_dir(Platform.INSTAGRAM)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text("Launch day", encoding="utf-8")

    assert cli.main(["run", "--real", "--platform", "instagram", "--interval", "0"]) == 0

    conn = open_app_db(settings.app_db_path)
    try:
        [job] = JobRepository(conn).list_recent(5)
    finally:
        conn.close()
    assert (job.status, job.platform_post_id, job.remote_ref, job.media_host_key) == (
        JobStatus.PUBLISHED, MEDIA_ID, CONTAINER_ID, None)
    assert meta.calls.count(("POST", f"/{USER_ID}/media_publish")) == 1
    assert meta.objects == {}  # The picture is deleted once the post is live.
    assert not (incoming / "launch.jpg").exists()  # Archived after a confirmed PUBLISHED.
    _no_secret_anywhere(settings, capsys.readouterr())


# Each platform's phrase works only for its own platform (API), every time (D46).


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, stability_interval_s=0)) as test_client:
        yield test_client


def _tomorrow(settings) -> str:
    return (datetime.now(settings.timezone) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")


@pytest.mark.parametrize(("platform", "wrong", "right"), [
    ("instagram", "publish to LinkedIn", "publish to Instagram"),
    ("linkedin", "publish to Instagram", "publish to LinkedIn"),
])
def test_a_phrase_never_enables_another_platform(settings, client, platform, wrong, right):
    run = client.post("/api/run", json={"platform": platform, "real": True, "confirm": wrong})
    assert run.status_code == 400 and f'"confirm": "{right}"' in run.json()["detail"]

    client.post("/api/posts", data={"name": "launch", "caption": "Hello", "platform": platform},
                files={"image": ("launch.jpg", JPEG, "image/jpeg")}, headers={"X-AutoPoster": "1"})
    body = {"name": "launch", "publish_at": _tomorrow(settings), "platform": platform, "real": True}
    assert client.post("/api/schedules", json={**body, "confirm": wrong}).status_code == 400
    created = client.post("/api/schedules", json={**body, "confirm": right})
    assert created.status_code == 201, created.text
    assert created.json()["schedule"]["mode"] == "real"

    # Re-confirming needs the phrase again; another platform's never works.
    schedule_id = created.json()["schedule"]["schedule_id"]
    again = client.post(f"/api/schedules/{schedule_id}/reconfirm", json={"real": True, "confirm": wrong})
    assert again.status_code == 400
    assert client.post(f"/api/schedules/{schedule_id}/reconfirm", json={"real": True}).status_code == 400
