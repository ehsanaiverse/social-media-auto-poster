"""D2: the API with Instagram registered. Simulated Instagram works; real Instagram is a 400 before anything happens."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from social_media_auto_poster.config import Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations import common
from video_fake import build_mp4

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
LOCAL = {"X-AutoPoster": "1"}
REFUSED = ("Real Instagram publishing is not available: it is forbidden until D48 is accepted (D49); "
           "nothing was published.")


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


def _tomorrow(settings: Settings) -> str:
    return (datetime.now(settings.timezone) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")


def _upload(client, platform="instagram"):
    return client.post("/api/posts", data={"name": "launch", "caption": "Launch day", "platform": platform},
                       files={"image": ("launch.jpg", JPEG, "image/jpeg")}, headers=LOCAL)


def test_simulated_instagram_works_end_to_end(settings, client):
    assert _upload(client).status_code == 201
    assert (settings.incoming_dir(Platform.INSTAGRAM) / "launch.jpg").is_file()

    scanned = client.get("/api/scan", params={"platform": "instagram"}).json()
    assert scanned["platform"] == "Instagram" and [c["name"] for c in scanned["candidates"]] == ["launch"]

    ran = client.post("/api/run", json={"platform": "instagram"})
    assert ran.status_code == 200, ran.text
    assert [(job["status"], job["mode"]) for job in ran.json()["jobs"]] == [("SIMULATED", "simulated")]


def test_a_simulated_instagram_schedule_is_accepted(settings, client):
    assert _upload(client).status_code == 201
    response = client.post("/api/schedules", json={"name": "launch", "publish_at": _tomorrow(settings),
                                                   "platform": "instagram"})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("request_kind", ["run", "schedule"])
def test_linkedins_phrase_never_enables_real_instagram(settings, client, request_kind):
    assert _upload(client).status_code == 201
    body = {"platform": "instagram", "real": True, "confirm": common.REAL_CONFIRMATION}
    if request_kind == "run":
        response = client.post("/api/run", json=body)
    else:
        response = client.post("/api/schedules", json={**body, "name": "launch", "publish_at": _tomorrow(settings)})

    noun, done = ("run", "published") if request_kind == "run" else ("schedule", "scheduled")
    assert response.status_code == 400 and response.json()["detail"] == (
        f'A real {noun} needs "confirm": "publish to Instagram" in the request. Nothing was {done}.')
    assert REFUSED  # The D49 text, kept for reference: real Instagram is no longer refused as such (D48).
    assert not settings.app_db_path.exists() and not settings.lock_file.exists()


def test_a_real_instagram_reconfirmation_needs_instagrams_phrase(settings, client):
    assert _upload(client).status_code == 201
    created = client.post("/api/schedules", json={"name": "launch", "publish_at": _tomorrow(settings),
                                                  "platform": "instagram"}).json()
    schedule_id = created["schedule"]["schedule_id"]

    response = client.post(f"/api/schedules/{schedule_id}/reconfirm",
                           json={"real": True, "confirm": common.REAL_CONFIRMATION})

    assert response.status_code == 400 and '"publish to Instagram"' in response.json()["detail"]
    schedules = client.get("/api/schedules").json()["schedules"]
    assert [(s["schedule_id"], s["mode"], s["status"]) for s in schedules] == [(schedule_id, "simulated", "SCHEDULED")]


def test_linkedin_requests_without_a_platform_are_unchanged(settings, client):
    response = client.post("/api/run", json={})
    assert response.status_code == 200 and response.json()["mode"] == "simulated"

# Reels (D51): a video is streamed to disk, accepted only for Instagram.


def _video_post(client, *, platform="instagram", name="reel", video=None):
    data = build_mp4(width=1080, height=1920)
    return client.post("/api/posts", data={"name": name, "caption": "Launch day", "platform": platform},
                       files={"image": ("reel.mp4", video if video is not None else data, "video/mp4")},
                       headers=LOCAL)


def test_a_reel_upload_is_streamed_into_incoming(settings, client):
    video = build_mp4(width=1080, height=1920)
    response = _video_post(client, video=video)

    assert response.status_code == 201, response.text
    incoming = settings.incoming_dir(Platform.INSTAGRAM)
    assert (incoming / "reel.mp4").read_bytes() == video
    assert (incoming / "reel.txt").read_text(encoding="utf-8") == "Launch day"
    assert list(incoming.glob(".upload-*")) == []  # No temporary files left behind.


def test_a_video_upload_is_refused_for_linkedin(settings, client):
    response = _video_post(client, platform="linkedin")

    assert response.status_code == 400
    assert response.json()["detail"] == "video not supported on LinkedIn"
    assert list(settings.incoming_dir(Platform.LINKEDIN).iterdir()) == []


def test_a_reel_is_checked_without_writing_anything(settings, client):
    response = client.post("/api/posts/check",
                           data={"name": "reel", "caption": "Launch day", "platform": "instagram"},
                           files={"image": ("reel.mp4", build_mp4(width=1080, height=1920), "video/mp4")},
                           headers=LOCAL)

    assert response.status_code == 200
    assert response.json()["ok"] is True and response.json()["errors"] == []
    assert list(settings.incoming_dir(Platform.INSTAGRAM).iterdir()) == []


def test_a_short_reel_is_refused_by_the_check(client):
    response = client.post("/api/posts/check",
                           data={"name": "reel", "caption": "Launch day", "platform": "instagram"},
                           files={"image": ("reel.mp4", build_mp4(duration=2000), "video/mp4")},
                           headers=LOCAL)

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert any("3 s to 15 min" in error for error in response.json()["errors"])