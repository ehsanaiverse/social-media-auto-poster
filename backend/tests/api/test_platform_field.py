"""The optional ``platform`` field of the API (A3b): LinkedIn by default; any other platform refused with 400.

Instagram and Facebook have no integration yet (D49 allows only simulated Instagram, and not before step B),
so every endpoint that takes the field refuses them before it takes the run lock or touches a folder or the
job database. Requests without the field behave exactly as before.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import schemas
from social_media_auto_poster.config import Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations import common

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
LOCAL = {"X-AutoPoster": "1"}

REFUSED = {
    "FACEBOOK": "Facebook is not supported yet. Supported: LinkedIn, Instagram. Nothing was done.",
    "x": "Unknown platform 'x'. Supported: LinkedIn, Instagram.",
}


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


def _upload(client, *, platform=None, name="launch"):
    data = {"name": name, "caption": "A caption."}
    if platform is not None:
        data["platform"] = platform
    return client.post("/api/posts", data=data, files={"image": (f"{name}.jpg", JPEG, "image/jpeg")}, headers=LOCAL)


def _tomorrow(settings: Settings) -> str:
    return (datetime.now(settings.timezone) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")


def _requests(settings: Settings):
    """Every endpoint that takes the field, as a function of the platform value."""
    return {
        "scan": lambda client, value: client.get("/api/scan", params={"platform": value}),
        "posts": lambda client, value: _upload(client, platform=value),
        "run": lambda client, value: client.post("/api/run", json={"platform": value}),
        "real run": lambda client, value: client.post(
            "/api/run", json={"platform": value, "real": True, "confirm": common.REAL_CONFIRMATION}
        ),
        "schedule": lambda client, value: client.post(
            "/api/schedules", json={"name": "launch", "publish_at": _tomorrow(settings), "platform": value}
        ),
        "real schedule": lambda client, value: client.post(
            "/api/schedules",
            json={"name": "launch", "publish_at": _tomorrow(settings), "platform": value, "real": True,
                  "confirm": common.REAL_CONFIRMATION},
        ),
    }


@pytest.mark.parametrize("endpoint", ["scan", "posts", "run", "real run", "schedule", "real schedule"])
@pytest.mark.parametrize("value", sorted(REFUSED))
def test_an_unsupported_or_unknown_platform_is_refused_before_anything_happens(settings, client, endpoint, value):
    response = _requests(settings)[endpoint](client, value)

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == REFUSED[value]
    assert not settings.lock_file.exists()
    assert not settings.app_db_path.exists()
    for platform in Platform:
        assert list(settings.incoming_dir(platform).iterdir()) == []


@pytest.mark.parametrize("value", [None, "", "LinkedIn", "linkedin", " LINKEDIN "])
def test_no_platform_or_linkedin_in_any_case_means_linkedin(settings, client, value):
    added = _upload(client, platform=value)
    assert added.status_code == 201, added.text
    assert (settings.incoming_dir(Platform.LINKEDIN) / "launch.jpg").is_file()

    params = {} if value is None else {"platform": value}
    scanned = client.get("/api/scan", params=params)
    assert scanned.status_code == 200
    assert scanned.json()["platform"] == "LinkedIn"
    assert [c["name"] for c in scanned.json()["candidates"]] == ["launch"]

    scheduled = client.post("/api/schedules", json={"name": "launch", "publish_at": _tomorrow(settings),
                                                   **({} if value is None else {"platform": value})})
    assert scheduled.status_code == 201, scheduled.text

    ran = client.post("/api/run", json={} if value is None else {"platform": value})
    assert ran.status_code == 200, ran.text
    assert ran.json()["scheduled"][0]["name"] == "launch"  # The scheduled post is held, as before.


def test_the_real_mode_phrase_has_a_single_source():
    assert schemas.REAL_CONFIRMATION is common.REAL_CONFIRMATION
    assert common.REAL_CONFIRMATION == "publish to LinkedIn"
