"""E2: ``GET /api/platforms``, ``POST /api/posts/check`` (writes nothing) and the API's resolve check."""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from instagram_fake import MEDIA_ID, json_response  # noqa: E402
from test_resolve_check import (  # noqa: E402, F401  (pytest fixtures)
    assert_read_only,
    checker_graph,
    faked,
    host,
    needs_review_job,
    settings,
    status,
    stored,
)

from app.main import create_app  # noqa: E402
from social_media_auto_poster.models import JobStatus, Platform  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (FIXTURES / "baseline-7x5.jpg").read_bytes()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
LOCAL = {"X-AutoPoster": "1"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, stability_interval_s=0)) as test_client:
        yield test_client


def _check(client, *, caption="Launch day", image=("launch.jpg", JPEG), **fields):
    return client.post("/api/posts/check", data={"caption": caption, **fields},
                       files={"image": (image[0], image[1], "application/octet-stream")}, headers=LOCAL)


def _every_file(settings) -> list[str]:
    return sorted(str(p.relative_to(settings.base_dir)) for p in settings.base_dir.rglob("*"))


# GET /api/platforms


def test_platforms_lists_the_backend_rules(client):
    body = client.get("/api/platforms").json()
    by_id = {p["id"]: p for p in body}
    assert list(by_id) == ["linkedin", "instagram"]

    linkedin, instagram = by_id["linkedin"], by_id["instagram"]
    assert (linkedin["name"], linkedin["real_available"], linkedin["confirmation_phrase"]) == \
        ("LinkedIn", True, "publish to LinkedIn")
    assert linkedin["rules"]["caption_max_chars"] == 3000 and linkedin["rules"]["aspect"] is None
    assert linkedin["rules"]["extensions"] == [".jpeg", ".jpg", ".png"]

    assert (instagram["name"], instagram["real_available"], instagram["confirmation_phrase"]) == \
        ("Instagram", True, "publish to Instagram")
    assert instagram["rules"] == {
        "extensions": [".jpeg", ".jpg", ".mov", ".mp4"], "max_image_bytes": 8_000_000,
        "max_video_bytes": 300 * 1024 * 1024, "max_caption_bytes": 64 * 1024,
        "caption_max_chars": 2200, "caption_max_hashtags": 30, "caption_max_mentions": 20,
        "aspect": {"min": "4:5", "max": "1.91:1"},
    }


# POST /api/posts/check


def test_check_accepts_a_good_post_and_writes_nothing(settings, client):
    before = _every_file(settings)

    response = _check(client, name="launch")

    assert response.status_code == 200, response.text
    assert response.json() == {"platform": "LinkedIn", "ok": True, "errors": []}
    assert _every_file(settings) == before
    assert not settings.app_db_path.exists()


def test_check_uses_each_platform_rules(settings, client):
    before = _every_file(settings)
    png = ("launch.png", PNG)

    assert _check(client, image=png).json()["ok"] is True
    refused = _check(client, image=png, platform="instagram").json()
    assert refused["platform"] == "Instagram" and refused["ok"] is False
    assert any("JPEG pictures only" in error for error in refused["errors"])

    too_long = _check(client, caption="x" * 2201, platform="Instagram").json()
    assert too_long["ok"] is False and len(too_long["errors"]) == 1
    assert _every_file(settings) == before


def test_check_reports_every_problem(client):
    body = _check(client, name="bad/name", caption="   ", image=("launch.gif", b"GIF89a")).json()
    assert body["ok"] is False
    assert body["errors"] == ["The name must not contain any of \"*/:<>?\\|.", "unsupported image type '.gif' "
                              "(allowed: .jpg, .jpeg, .png)", "caption is empty"]


def test_a_lone_surrogate_in_the_caption_is_a_clear_400(settings, client):
    before = _every_file(settings)
    # A lone surrogate cannot come from the form encoder; it can come from a JSON-like client, so call directly.
    from app.routers import posts
    import asyncio

    class Upload:
        filename = "launch.jpg"

        async def read(self, size):
            return JPEG

    service = client.app.state.job_service
    with pytest.raises(HTTPException) as raised:
        asyncio.run(posts.check_post(caption="hi \ud800", image=Upload(), name=None, platform=None, service=service))
    assert raised.value.status_code == 400
    assert raised.value.detail == "The caption is not valid text."
    assert _every_file(settings) == before


def test_check_needs_the_local_header_and_a_known_platform(client):
    no_header = client.post("/api/posts/check", data={"caption": "x"}, files={"image": ("a.jpg", JPEG)})
    assert no_header.status_code == 403
    assert _check(client, platform="facebook").status_code == 400
    assert _check(client, platform="tiktok").status_code == 400


# POST /api/jobs/{id}/resolve with {"check": true}


def _published(faked):
    faked.answers["status"] = status("PUBLISHED")
    faked.answers["media"] = json_response(200, {"data": [{"id": MEDIA_ID, "media_type": "IMAGE",
                                                           "timestamp": "2099-01-01T00:00:00+0000"}]})


def test_the_api_check_settles_a_published_job_read_only(settings, host, faked, checker_graph, client):
    job_id = needs_review_job(settings, host)
    _published(faked)

    response = client.post(f"/api/jobs/{job_id[:8]}/resolve", json={"check": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["changed"], body["post_id"]) == ("PUBLISHED", True, MEDIA_ID)
    assert body["files_at"].startswith("Instagram")  # Relative to the data folder: never an absolute path.
    assert stored(settings, job_id).status is JobStatus.PUBLISHED
    assert_read_only(checker_graph)


def test_an_uncertain_api_check_changes_nothing(settings, host, faked, checker_graph, client):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = status("IN_PROGRESS")

    body = client.post(f"/api/jobs/{job_id[:8]}/resolve", json={"check": True}).json()

    assert (body["status"], body["changed"]) == ("NEEDS_REVIEW", False)
    assert stored(settings, job_id).status is JobStatus.NEEDS_REVIEW
    assert_read_only(checker_graph)


def test_the_api_check_is_refused_without_instagram_credentials(settings, host, client):
    job_id = needs_review_job(settings, host)  # No fake checker and no credentials in backend\.env.

    response = client.post(f"/api/jobs/{job_id[:8]}/resolve", json={"check": True})

    assert response.status_code == 400
    assert "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set" in response.json()["detail"]
    assert stored(settings, job_id).status is JobStatus.NEEDS_REVIEW


def test_check_cannot_be_combined_with_another_answer(settings, host, client):
    job_id = needs_review_job(settings, host)
    for body in ({"check": True, "failed": True}, {"check": True, "published_post_id": "x"}):
        response = client.post(f"/api/jobs/{job_id[:8]}/resolve", json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "Send only one of published_post_id, failed: true or check: true."
    assert stored(settings, job_id).status is JobStatus.NEEDS_REVIEW
    assert stored(settings, job_id).platform is Platform.INSTAGRAM


# Step F: the dashboard lists one platform's jobs and schedules


def test_jobs_and_schedules_can_be_listed_per_platform(settings, client):
    for platform in ("linkedin", "instagram"):
        client.post("/api/posts", data={"name": f"{platform}-post", "caption": "Hello", "platform": platform},
                    files={"image": ("a.jpg", JPEG, "image/jpeg")}, headers=LOCAL)
        assert client.post("/api/run", json={"platform": platform}).status_code == 200

    every = [job["stem"] for job in client.get("/api/jobs").json()]
    assert sorted(every) == ["instagram-post", "linkedin-post"]  # Without platform: every platform, as before.
    only = client.get("/api/jobs", params={"platform": "Instagram"}).json()
    assert [job["stem"] for job in only] == ["instagram-post"]
    assert client.get("/api/schedules", params={"platform": "instagram"}).json()["schedules"] == []
    assert client.get("/api/jobs", params={"platform": "facebook"}).status_code == 400
