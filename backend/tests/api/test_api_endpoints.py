"""Tests for every endpoint of the local API (``backend/app``), through FastAPI's TestClient.

Everything runs in a temporary base folder with its own data folder and job database.
No real network is used: the LinkedIn transport is ``httpx.MockTransport``, and the
conftest blocks any other request. Real publishing is never started; the tests that
reach that point replace the publisher with a fake that fails if it is ever called.
"""

import shutil
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.__main__ import HOST, PORT
from app.main import create_app
from social_media_auto_poster import main as core
from social_media_auto_poster.config import Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.ingestion.intake import MAX_IMAGE_BYTES
from social_media_auto_poster.ingestion.validation import MAX_CAPTION_BYTES
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

from linkedin_fake import POST_ID, FakeLinkedIn

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
PNG = (IMAGES / "image-6x3.png").read_bytes()
LOCAL = {"X-AutoPoster": "1"}  # The custom header the upload requires (api-plan rule 7).
LINKEDIN_TOKEN = "TEST-TOKEN-must-never-leak-api"


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = tmp_path / "base"
    base.mkdir()
    settings = load_settings(base_dir=base, environ={})
    ensure_data_dirs(settings)
    return settings


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, stability_interval_s=0)) as test_client:
        yield test_client


def incoming(settings: Settings) -> Path:
    return settings.incoming_dir(Platform.LINKEDIN)


def upload(client, name="launch", image=JPEG, filename="launch.jpg", caption="A caption.", headers=LOCAL):
    return client.post(
        "/api/posts",
        data={"name": name, "caption": caption},
        files={"image": (filename, image, "image/jpeg")},
        headers=headers,
    )


def add_post(client, name="launch", **kwargs):
    response = upload(client, name=name, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def run(client, body=None):
    return client.post("/api/run", json={} if body is None else body)


def seed_job(settings, *, mode, status, errors=()):
    """Insert a job row directly (for tests that need a job in a given state)."""
    job_id = str(uuid.uuid4())
    conn = open_app_db(settings.app_db_path)
    try:
        JobRepository(conn).create(
            job_id=job_id,
            platform=Platform.LINKEDIN,
            mode=mode,
            stem="old-post",
            media_name="old-post.jpg",
            caption_name="old-post.txt",
            media_sha256="a" * 64,
            caption_sha256="b" * 64,
            status=status,
            errors=tuple(errors),
        )
    finally:
        conn.close()
    return job_id


# health


def test_health_reports_simulated_default_and_no_secrets(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["simulated_by_default"] is True
    assert body["linkedin_version"]


def test_the_server_binds_to_localhost_only():
    assert HOST == "127.0.0.1"
    assert PORT == 8000


# POST /api/posts


def test_upload_adds_the_post_to_incoming_with_the_expected_names(client, settings):
    body = add_post(client, name="launch")
    assert body == {
        "name": "launch",
        "files": {"image": "launch.jpg", "caption": "launch.txt"},
        "duplicate_of": None,
    }
    assert (incoming(settings) / "launch.jpg").read_bytes() == JPEG
    assert (incoming(settings) / "launch.txt").read_text(encoding="utf-8") == "A caption."
    assert not list(incoming(settings).glob(".upload-*"))  # Temporary files are cleaned up.


def test_upload_needs_the_custom_header(client, settings):
    response = upload(client, headers={})
    assert response.status_code == 403
    assert list(incoming(settings).iterdir()) == []


def test_upload_refuses_an_unsupported_image_type(client, settings):
    response = upload(client, filename="launch.gif")
    assert response.status_code == 400
    assert "jpg" in response.json()["detail"].lower()
    assert list(incoming(settings).iterdir()) == []


def test_upload_refuses_a_png_renamed_to_jpg(client, settings):
    response = upload(client, image=PNG, filename="launch.jpg")
    assert response.status_code == 400
    assert "PNG" in response.json()["detail"]
    assert ".upload-" not in response.json()["detail"]  # The temporary name is never shown to the user.
    assert list(incoming(settings).iterdir()) == []


def test_upload_refuses_an_empty_caption(client, settings):
    response = upload(client, caption="   ")
    assert response.status_code == 400
    assert list(incoming(settings).iterdir()) == []


def test_upload_refuses_a_caption_over_3000_characters(client, settings):
    response = upload(client, caption="x" * 3001)
    assert response.status_code == 400
    assert "caption is 3001 characters; LinkedIn allows at most 3000" in response.json()["detail"]
    assert list(incoming(settings).iterdir()) == []

    assert upload(client, name="limit", caption="y" * 3000).status_code == 201
    assert upload(client, name="emoji", caption="🚀" * 1500 + "x").status_code == 400  # Emoji count as two.


def test_upload_refuses_a_name_the_scanner_would_ignore_or_that_has_forbidden_characters(client):
    assert upload(client, name="bad/name").status_code == 400
    assert upload(client, name="   ").status_code == 400
    assert upload(client, name=".hidden").status_code == 400


def test_upload_never_overwrites_an_existing_post(client):
    add_post(client, name="launch")
    response = upload(client, name="LAUNCH")  # Same name, different case: the scanner treats it as the same post.
    assert response.status_code == 409


def test_upload_refuses_an_image_over_the_size_limit(client, settings):
    too_big = b"\xff\xd8\xff" + b"0" * MAX_IMAGE_BYTES
    response = upload(client, image=too_big)
    assert response.status_code == 413
    assert list(incoming(settings).iterdir()) == []


def test_upload_refuses_a_caption_over_the_size_limit(client, settings):
    response = upload(client, caption="a" * (MAX_CAPTION_BYTES + 1))
    assert response.status_code == 413
    assert list(incoming(settings).iterdir()) == []


def test_upload_reports_a_duplicate_of_an_existing_job(client, settings):
    add_post(client, name="first")
    assert run(client).status_code == 200  # Simulated: the post stays in incoming/ with a SIMULATED job.
    body = add_post(client, name="second")  # Same bytes, different name.
    assert body["duplicate_of"] is not None
    assert body["duplicate_of"] == client.get("/api/jobs").json()[0]["job_id"]


def test_upload_fails_when_the_incoming_folder_is_missing(client, settings):
    shutil.rmtree(incoming(settings))
    response = upload(client)
    assert response.status_code == 404


# GET /api/scan


def test_scan_of_an_empty_folder_lists_nothing(client):
    body = client.get("/api/scan").json()
    assert body["candidates"] == []
    assert body["pending"] == [] and body["ambiguous"] == []


def test_scan_lists_a_stable_valid_post_as_new(client):
    add_post(client, name="launch")
    [candidate] = client.get("/api/scan").json()["candidates"]
    assert candidate["name"] == "launch"
    assert candidate["stable"] is True
    assert candidate["valid"] is True
    assert candidate["duplicate_of"] is None
    assert candidate["only_simulated"] is False


def test_scan_reports_a_lone_image_as_pending(client, settings):
    (incoming(settings) / "waiting.jpg").write_bytes(JPEG)
    body = client.get("/api/scan").json()
    assert body["candidates"] == []
    assert body["pending"] == ["waiting.jpg"]


def test_scan_reports_a_simulated_post_in_real_mode(client):
    add_post(client, name="launch")
    assert run(client).status_code == 200
    [candidate] = client.get("/api/scan", params={"real": True}).json()["candidates"]
    assert candidate["only_simulated"] is True  # A real run would refuse it without --publish-simulated.
    assert candidate["duplicate_of"] is None


def test_scan_of_a_missing_folder_is_a_404(client, settings):
    shutil.rmtree(incoming(settings))
    assert client.get("/api/scan").status_code == 404


def test_scan_never_moves_or_publishes_anything(client, settings):
    add_post(client, name="launch")
    before = sorted(p.name for p in incoming(settings).iterdir())
    client.get("/api/scan")
    client.get("/api/scan", params={"real": True})
    assert sorted(p.name for p in incoming(settings).iterdir()) == before
    assert not settings.app_db_path.exists()


# POST /api/run


def test_run_is_simulated_by_default_and_says_so(client, settings):
    add_post(client, name="launch")
    response = run(client)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "simulated"
    assert body["simulated"] is True
    [job] = body["jobs"]
    assert job["status"] == "SIMULATED"
    assert job["stem"] == "launch"
    assert (incoming(settings) / "launch.jpg").exists()  # Simulated posts stay in incoming/.


def test_run_requires_a_json_body(client, settings):
    add_post(client, name="launch")
    response = client.post("/api/run", content=b"{}", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_real_mode_cannot_start_without_the_explicit_flag_and_confirmation(client, settings, monkeypatch):
    add_post(client, name="launch")
    publisher_calls = []
    monkeypatch.setattr(core, "_real_publisher", lambda s: publisher_calls.append(s) or _must_not_publish())

    # Without real: true, the run is simulated even if a confirmation is sent.
    assert run(client, {"real": False, "confirm": "publish to LinkedIn"}).json()["mode"] == "simulated"

    # real: true without the confirmation text is refused, and nothing is processed.
    response = run(client, {"real": True})
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]
    response = run(client, {"real": True, "confirm": "yes please"})
    assert response.status_code == 400

    # publish_simulated only applies to real runs.
    assert run(client, {"publish_simulated": True}).status_code == 400
    assert publisher_calls == []
    assert (incoming(settings) / "launch.jpg").exists()


def test_real_run_without_a_token_is_refused_before_anything_is_sent(client, settings):
    add_post(client, name="launch")
    response = run(client, {"real": True, "confirm": "publish to LinkedIn"})
    assert response.status_code == 412
    detail = response.json()["detail"]
    assert "LINKEDIN_ACCESS_TOKEN" in detail
    assert "nothing was published" in detail.lower()
    assert (incoming(settings) / "launch.jpg").exists()
    assert client.get("/api/jobs").json() == []  # The refusal happens before any job is recorded.


def test_real_run_refuses_posts_that_were_only_simulated(client, settings, monkeypatch):
    add_post(client, name="launch")
    assert run(client).status_code == 200  # Simulated first.
    monkeypatch.setattr(core, "_real_publisher", lambda s: NoPublishPublisher())
    response = run(client, {"real": True, "confirm": "publish to LinkedIn"})
    assert response.status_code == 412
    assert "only simulated" in response.json()["detail"]
    assert (incoming(settings) / "launch.jpg").exists()


def test_env_can_never_switch_an_api_run_to_real_mode(client, settings, monkeypatch):
    # A complete, valid-looking LinkedIn setup, plus would-be switches, in backend/.env and the real environment.
    switches = {"AUTOPOSTER_MODE": "real", "AUTOPOSTER_REAL": "1"}
    env_lines = [f"LINKEDIN_ACCESS_TOKEN={LINKEDIN_TOKEN}", f"LINKEDIN_TOKEN_CREATED={date.today().isoformat()}"]
    env_text = "\n".join(env_lines + [f"{k}={v}" for k, v in switches.items()]) + "\n"
    (settings.base_dir / ".env").write_text(env_text, encoding="utf-8")
    for name, value in switches.items():
        monkeypatch.setenv(name, value)
    # The real _real_publisher, watched: if it were ever called, it would reach the fake LinkedIn.
    fake = FakeLinkedIn()
    monkeypatch.setattr(core, "_linkedin_client", lambda token: LinkedInClient(token, transport=httpx.MockTransport(fake)))
    real_publisher_calls = []
    original = core._real_publisher
    monkeypatch.setattr(core, "_real_publisher", lambda s: real_publisher_calls.append(s) or original(s))
    add_post(client, name="launch")

    response = run(client)

    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "simulated"
    assert [(job["stem"], job["mode"], job["status"]) for job in client.get("/api/jobs").json()] == [
        ("launch", "simulated", "SIMULATED")
    ]
    assert real_publisher_calls == []
    assert fake.requests == []

    # Control: the same setup with the typed confirmation does reach the fake LinkedIn
    # (then refuses the only-simulated post).
    assert run(client, {"real": True, "confirm": "publish to LinkedIn"}).status_code == 412
    assert len(real_publisher_calls) == 1 and fake.requests
    assert fake.calls("POST", "/rest/posts") == []


def schedule_in_database(settings, stem: str, *, mode: PublishMode) -> None:
    """A SCHEDULED row for a post already in incoming/ (the schedule command itself is tested in the CLI tests)."""
    from social_media_auto_poster.services.hashing import sha256_file
    from social_media_auto_poster.storage.schedules import ScheduleRepository

    folder = incoming(settings)
    conn = open_app_db(settings.app_db_path)
    try:
        ScheduleRepository(conn).create(
            schedule_id=str(uuid.uuid4()), platform=Platform.LINKEDIN, mode=mode, stem=stem,
            media_name=f"{stem}.jpg", caption_name=f"{stem}.txt",
            media_sha256=sha256_file(folder / f"{stem}.jpg"), caption_sha256=sha256_file(folder / f"{stem}.txt"),
            publish_at=datetime(2030, 1, 1, 4, 0, tzinfo=UTC),
        )
    finally:
        conn.close()


def test_an_api_run_skips_a_scheduled_post_and_lists_it(client, settings):
    add_post(client, name="launch")
    add_post(client, name="other", caption="Another caption.")
    schedule_in_database(settings, "launch", mode=PublishMode.SIMULATED)

    response = run(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [job["stem"] for job in body["jobs"]] == ["other"]
    assert [(s["name"], s["mode"], s["publish_at"]) for s in body["scheduled"]] == [
        ("launch", "simulated", "2030-01-01T04:00:00+00:00")
    ]
    assert body["scheduled"][0]["publish_at_local"] == "2030-01-01 09:00 Asia/Karachi"
    assert (incoming(settings) / "launch.jpg").exists()


def test_a_real_api_run_never_publishes_a_scheduled_post(client, settings, monkeypatch):
    add_post(client, name="launch")
    schedule_in_database(settings, "launch", mode=PublishMode.REAL)
    fake = FakeLinkedIn()
    use_fake_linkedin(monkeypatch, fake)

    response = run(client, REAL)

    assert response.status_code == 200, response.text
    assert response.json()["jobs"] == []
    assert [s["name"] for s in response.json()["scheduled"]] == ["launch"]
    assert fake.calls("POST", "/rest/posts") == []


def test_run_is_refused_with_409_while_another_run_holds_the_lock(client, settings):
    add_post(client, name="launch")
    with single_run_lock(settings.lock_file):
        response = run(client)
    assert response.status_code == 409
    assert (incoming(settings) / "launch.jpg").exists()


def _must_not_publish():
    raise AssertionError("the publisher must not be used in this test")


class NoPublishPublisher:
    """Stands in for LinkedIn in a test: any attempt to publish fails the test."""

    def publish(self, *args, **kwargs):
        raise AssertionError("the publisher must not be used in this test")


# GET /api/jobs and GET /api/jobs/{id}


def test_jobs_is_an_empty_list_before_any_run(client):
    assert client.get("/api/jobs").json() == []


def test_jobs_lists_recent_runs_and_filters_by_status(client):
    add_post(client, name="launch")
    run(client)
    jobs = client.get("/api/jobs").json()
    assert [j["status"] for j in jobs] == ["SIMULATED"]
    assert client.get("/api/jobs", params={"status": "simulated"}).json() == jobs
    assert client.get("/api/jobs", params={"status": "PUBLISHED"}).json() == []


def test_jobs_rejects_an_unknown_status_and_a_bad_limit(client):
    assert client.get("/api/jobs", params={"status": "SUCCESS"}).status_code == 400
    assert client.get("/api/jobs", params={"last": 0}).status_code == 422


def test_one_job_by_prefix_and_its_errors(client, settings):
    add_post(client, name="launch")
    run(client)
    [job] = client.get("/api/jobs").json()
    response = client.get(f"/api/jobs/{job['job_id'][:8]}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["job_id"] == job["job_id"]
    assert detail["media_name"] == "launch.jpg"
    assert detail["media_sha256"] and detail["caption_sha256"]


def test_one_job_refuses_short_prefixes_and_unknown_ids(client, settings):
    add_post(client, name="launch")
    run(client)
    assert client.get("/api/jobs/abc").status_code == 400
    assert client.get("/api/jobs/zzzzzzzz").status_code == 404


def test_destinations_are_shown_relative_to_the_data_folder(client, settings):
    job_id = seed_job(settings, mode=PublishMode.REAL, status=JobStatus.PUBLISHED)
    archived = settings.archive_dir(Platform.LINKEDIN) / "20261028T000000Z_old-post"
    conn = open_app_db(settings.app_db_path)
    try:
        JobRepository(conn).update(job_id, destination=str(archived))
    finally:
        conn.close()
    assert client.get(f"/api/jobs/{job_id}").json()["destination"] == str(Path("LinkedIn") / "archive" / archived.name)
    assert str(settings.base_dir) not in client.get("/api/jobs").text


def test_one_job_without_a_database_is_a_404(client):
    assert client.get("/api/jobs/abcdef12").status_code == 404


# POST /api/jobs/{id}/resolve


def test_resolve_a_failed_job_so_it_can_be_retried(client, settings):
    job_id = seed_job(settings, mode=PublishMode.REAL, status=JobStatus.NEEDS_REVIEW)
    response = client.post(f"/api/jobs/{job_id[:8]}/resolve", json={"failed": True})
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id and body["status"] == "FAILED"
    assert "resolved manually as FAILED" in body["note"]


def test_resolve_as_published_records_the_post_id(client, settings):
    job_id = seed_job(settings, mode=PublishMode.REAL, status=JobStatus.NEEDS_REVIEW)
    response = client.post(f"/api/jobs/{job_id}/resolve", json={"published_post_id": "urn:li:share:1"})
    assert response.status_code == 200
    assert response.json()["status"] == "PUBLISHED"
    assert client.get(f"/api/jobs/{job_id}").json()["post_id"] == "urn:li:share:1"


def test_resolve_refuses_to_mark_a_simulated_job_as_published(client, settings):
    job_id = seed_job(settings, mode=PublishMode.SIMULATED, status=JobStatus.NEEDS_REVIEW)
    response = client.post(f"/api/jobs/{job_id}/resolve", json={"published_post_id": "urn:li:share:1"})
    assert response.status_code == 422


def test_resolve_refuses_a_job_that_is_not_waiting_for_review(client, settings):
    job_id = seed_job(settings, mode=PublishMode.SIMULATED, status=JobStatus.SIMULATED)
    response = client.post(f"/api/jobs/{job_id}/resolve", json={"failed": True})
    assert response.status_code == 409


def test_resolve_needs_exactly_one_outcome_and_a_real_post_id(client, settings):
    job_id = seed_job(settings, mode=PublishMode.REAL, status=JobStatus.NEEDS_REVIEW)
    url = f"/api/jobs/{job_id}/resolve"
    assert client.post(url, json={}).status_code == 400
    assert client.post(url, json={"failed": True, "published_post_id": "x"}).status_code == 400
    assert client.post(url, json={"published_post_id": "   "}).status_code == 400
    assert client.post(url, content=b'{"failed": true}', headers={"Content-Type": "text/plain"}).status_code == 415


def test_resolve_of_an_unknown_job_is_a_404(client, settings):
    seed_job(settings, mode=PublishMode.REAL, status=JobStatus.NEEDS_REVIEW)
    assert client.post("/api/jobs/ffffffff/resolve", json={"failed": True}).status_code == 404


def test_resolve_is_refused_with_409_while_a_run_holds_the_lock(client, settings):
    job_id = seed_job(settings, mode=PublishMode.REAL, status=JobStatus.NEEDS_REVIEW)
    with single_run_lock(settings.lock_file):
        response = client.post(f"/api/jobs/{job_id}/resolve", json={"failed": True})
    assert response.status_code == 409


# GET /api/linkedin/status


def test_status_without_a_token_says_missing_and_asks_nobody(client):
    body = client.get("/api/linkedin/status").json()
    assert body["token_set"] is False
    assert body["state"] == "missing"
    assert body["member"] is None


def test_status_reports_expiry_but_never_the_token(client, monkeypatch):
    today = date.today()
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", LINKEDIN_TOKEN)
    monkeypatch.setenv("LINKEDIN_TOKEN_CREATED", today.isoformat())
    response = client.get("/api/linkedin/status")
    body = response.json()
    assert body["token_set"] is True
    assert body["state"] == "valid"
    assert body["days_left"] == 60
    assert body["expires_on"] == (today.fromordinal(today.toordinal() + 60)).isoformat()
    assert LINKEDIN_TOKEN not in response.text


def test_status_with_check_asks_linkedin_and_shows_the_member_not_the_token(client, settings, monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", LINKEDIN_TOKEN)
    monkeypatch.setenv("LINKEDIN_TOKEN_CREATED", date.today().isoformat())
    seen = []

    def linkedin(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"sub": "member-123", "name": "Test Person"})

    monkeypatch.setattr(core, "_linkedin_client", lambda token: LinkedInClient(token, transport=httpx.MockTransport(linkedin)))
    response = client.get("/api/linkedin/status", params={"check": True})
    assert response.status_code == 200
    body = response.json()
    assert body["member"] == {"name": "Test Person", "urn": "urn:li:person:member-123"}
    assert seen == [("GET", "/v2/userinfo")]
    assert LINKEDIN_TOKEN not in response.text


def test_status_check_is_a_502_when_linkedin_cannot_be_reached(client, monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", LINKEDIN_TOKEN)
    monkeypatch.setenv("LINKEDIN_TOKEN_CREATED", date.today().isoformat())

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(core, "_linkedin_client", lambda token: LinkedInClient(token, transport=httpx.MockTransport(unreachable)))
    response = client.get("/api/linkedin/status", params={"check": True})
    assert response.status_code == 502
    assert LINKEDIN_TOKEN not in response.text


def test_status_with_check_does_not_call_linkedin_for_an_expired_token(client, monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", LINKEDIN_TOKEN)
    monkeypatch.setenv("LINKEDIN_TOKEN_CREATED", "2020-01-01")
    monkeypatch.setattr(core, "_linkedin_client", lambda token: _must_not_publish())
    body = client.get("/api/linkedin/status", params={"check": True}).json()
    assert body["state"] == "expired"
    assert LINKEDIN_TOKEN not in str(body)


# Real runs through POST /api/run: the token never comes back
#
# The whole real path runs: the token comes from the environment (as from backend/.env),
# is checked with /v2/userinfo, and the LinkedIn publisher uploads and posts. Only the
# transport is fake (FakeLinkedIn); nothing reaches the network.

REAL = {"real": True, "confirm": "publish to LinkedIn"}
# A LinkedIn answer that echoes the token, in the message and in the error code.
ECHOES_TOKEN = {"message": f"bad request from {LINKEDIN_TOKEN}", "code": LINKEDIN_TOKEN, "status": 0}


def use_fake_linkedin(monkeypatch, fake: FakeLinkedIn, token: str = LINKEDIN_TOKEN) -> None:
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", token)
    monkeypatch.setenv("LINKEDIN_TOKEN_CREATED", date.today().isoformat())
    monkeypatch.setattr(core, "_linkedin_client", lambda token: LinkedInClient(token, transport=httpx.MockTransport(fake)))


def assert_token_sent_but_never_returned(client, fake: FakeLinkedIn, run_response, token: str = LINKEDIN_TOKEN) -> dict:
    """The token really was used (sent to the fake LinkedIn), yet no API response contains it. Returns the job."""
    assert fake.requests and all(r.headers["Authorization"] == f"Bearer {token}" for r in fake.requests)
    assert token not in run_response.text
    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert token not in jobs.text
    assert len(jobs.json()) == 1
    detail = client.get(f"/api/jobs/{jobs.json()[0]['job_id']}")
    assert detail.status_code == 200
    assert token not in detail.text
    return detail.json()


def test_real_run_success_never_returns_the_token(client, settings, monkeypatch):
    add_post(client, name="launch")
    fake = FakeLinkedIn()
    use_fake_linkedin(monkeypatch, fake)

    response = run(client, REAL)

    assert response.status_code == 200, response.text
    assert response.json()["exit_code"] == 0
    job = assert_token_sent_but_never_returned(client, fake, response)
    assert job["status"] == "PUBLISHED"
    assert job["post_id"] == POST_ID
    assert len(fake.calls("POST", "/rest/posts")) == 1


def test_real_run_failure_never_returns_the_token_even_if_linkedin_echoes_it(client, settings, monkeypatch):
    add_post(client, name="launch")
    fake = FakeLinkedIn(post=httpx.Response(422, json=ECHOES_TOKEN))
    use_fake_linkedin(monkeypatch, fake)

    response = run(client, REAL)

    assert response.status_code == 200, response.text
    assert response.json()["exit_code"] == 3
    job = assert_token_sent_but_never_returned(client, fake, response)
    assert job["status"] == "FAILED"
    assert any("LinkedIn answered 422" in error for error in job["errors"])
    report = settings.data_dir / job["destination"] / "error-report.txt"
    assert report.is_file()
    assert LINKEDIN_TOKEN not in report.read_text(encoding="utf-8")
    assert len(fake.calls("POST", "/rest/posts")) == 1


def test_real_run_uncertain_outcome_never_returns_the_token_even_if_linkedin_echoes_it(client, settings, monkeypatch):
    add_post(client, name="launch")
    fake = FakeLinkedIn(post=httpx.Response(500, json=ECHOES_TOKEN))
    use_fake_linkedin(monkeypatch, fake)

    response = run(client, REAL)

    assert response.status_code == 200, response.text
    assert response.json()["exit_code"] == 3
    job = assert_token_sent_but_never_returned(client, fake, response)
    assert job["status"] == "NEEDS_REVIEW"
    assert any("LinkedIn answered 500" in error for error in job["errors"])
    report = settings.data_dir / job["destination"] / "error-report.txt"
    assert report.is_file()
    assert LINKEDIN_TOKEN not in report.read_text(encoding="utf-8")
    assert len(fake.calls("POST", "/rest/posts")) == 1  # Never retried.


def test_real_run_failure_redacts_an_alphanumeric_token_that_linkedin_echoes_in_the_code(client, settings, monkeypatch):
    # Letters and digits only, and short: it passes the error-code rules, so only redaction keeps it out.
    token = "AQVtesttoken1234567890abcdef"
    add_post(client, name="launch")
    fake = FakeLinkedIn(post=httpx.Response(422, json={"code": token, "message": f"bad request from {token}"}))
    use_fake_linkedin(monkeypatch, fake, token=token)

    response = run(client, REAL)

    assert response.status_code == 200, response.text
    assert response.json()["exit_code"] == 3
    job = assert_token_sent_but_never_returned(client, fake, response, token=token)
    assert job["status"] == "FAILED"
    assert any("LinkedIn answered 422 [redacted]" in error for error in job["errors"])
    report = settings.data_dir / job["destination"] / "error-report.txt"
    assert report.is_file()
    assert token not in report.read_text(encoding="utf-8")
    log = settings.log_file.read_text(encoding="utf-8")
    assert "Run finished" in log  # The log really was written by this run.
    assert token not in log
