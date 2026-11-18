"""Tests for the schedules endpoints (D46): ``GET/POST /api/schedules``, ``POST /api/schedules/{id}/cancel``
and ``POST /api/schedules/{id}/reconfirm``, plus the schedule fields of ``GET /api/scan``.

They call the same ``operations.schedules`` functions as the CLI. Every test has its own base and data folder
and a fixed clock; no network is used (the conftest blocks it) and nothing is ever published.
"""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from social_media_auto_poster.config import Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations import schedules as schedules_ops
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage import database
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.schedules import ScheduleRepository

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
NOW = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)  # 11:00 in Asia/Karachi.
AT = "2026-11-20 12:00"  # One hour ahead, local time.
PHRASE = "publish to LinkedIn"
TOKEN = "TEST-TOKEN-must-never-leak-schedules"


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setattr(clock, "now", lambda: NOW)
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


def add_post(settings: Settings, stem: str = "launch", caption: str = "A caption.") -> None:
    (incoming(settings) / f"{stem}.jpg").write_bytes(JPEG)
    (incoming(settings) / f"{stem}.txt").write_text(caption, encoding="utf-8")


def create(client, name="launch", at=AT, **extra):
    return client.post("/api/schedules", json={"name": name, "publish_at": at, **extra})


def created(client, name="launch", at=AT, **extra) -> dict:
    response = create(client, name, at, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def rows(settings: Settings) -> dict[str, tuple[str, str]]:
    """schedule_id -> (status, mode), straight from the database."""
    conn = open_app_db(settings.app_db_path)
    try:
        return {r.schedule_id: (str(r.status), str(r.mode)) for r in ScheduleRepository(conn).list_recent(50)}
    finally:
        conn.close()


def make_v1_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN; {database.MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
    conn.close()


def user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


# GET /api/schedules


def test_list_without_a_database_is_empty_and_creates_nothing(client, settings):
    response = client.get("/api/schedules")
    assert response.status_code == 200
    assert response.json() == {"timezone": "Asia/Karachi", "available": False, "schedules": []}
    assert not settings.app_db_path.exists()


def test_list_of_a_version_1_database_does_not_upgrade_it(client, settings):
    make_v1_database(settings.app_db_path)
    response = client.get("/api/schedules")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert user_version(settings.app_db_path) == 1
    assert not list(settings.app_db_path.parent.glob("*.bak-*"))


def test_create_then_list_shows_the_schedule_with_local_time_and_content_state(client, settings):
    add_post(settings)
    body = created(client)
    schedule = body["schedule"]
    assert schedule["name"] == "launch"
    assert schedule["mode"] == "simulated"
    assert schedule["status"] == "SCHEDULED"
    assert schedule["publish_at"] == "2026-11-20T07:00:00+00:00"
    assert schedule["publish_at_local"] == "2026-11-20 12:00 Asia/Karachi"
    assert body["warnings"] == []

    listed = client.get("/api/schedules").json()
    assert listed["available"] is True
    assert [(s["schedule_id"], s["content_state"]) for s in listed["schedules"]] == [(schedule["schedule_id"], "ok")]


def test_list_reports_changed_and_missing_content(client, settings):
    add_post(settings, "changed")
    add_post(settings, "missing", caption="Another caption.")
    created(client, "changed")
    created(client, "missing")
    (incoming(settings) / "changed.txt").write_text("Edited after scheduling.", encoding="utf-8")
    (incoming(settings) / "missing.jpg").unlink()

    states = {s["name"]: s["content_state"] for s in client.get("/api/schedules").json()["schedules"]}
    assert states == {"changed": "changed", "missing": "missing"}


def test_list_all_and_by_status(client, settings):
    add_post(settings)
    first = created(client)["schedule"]["schedule_id"]
    assert client.post(f"/api/schedules/{first}/cancel", json={}).status_code == 200
    second = created(client)["schedule"]["schedule_id"]

    active = client.get("/api/schedules").json()["schedules"]
    assert [s["schedule_id"] for s in active] == [second]
    everything = client.get("/api/schedules?all=true").json()["schedules"]
    assert {s["schedule_id"] for s in everything} == {first, second}
    cancelled = client.get("/api/schedules?status=cancelled").json()["schedules"]
    assert [(s["schedule_id"], s["status"], s["content_state"]) for s in cancelled] == [(first, "CANCELLED", None)]


def test_list_rejects_an_unknown_status(client):
    response = client.get("/api/schedules?status=LATER")
    assert response.status_code == 400
    assert "LATER" in response.json()["detail"]


# POST /api/schedules


def test_create_is_simulated_by_default(client, settings):
    add_post(settings)
    schedule_id = created(client)["schedule"]["schedule_id"]
    assert rows(settings) == {schedule_id: ("SCHEDULED", "simulated")}


def test_create_real_needs_the_typed_phrase(client, settings):
    add_post(settings)
    for extra in ({"real": True}, {"real": True, "confirm": "yes"}, {"real": True, "confirm": "Publish to linkedin"}):
        response = create(client, **extra)
        assert response.status_code == 400, extra
        assert PHRASE in response.json()["detail"]
        assert "Nothing was scheduled" in response.json()["detail"]
    assert not settings.app_db_path.exists()

    body = created(client, real=True, confirm=PHRASE)
    assert body["schedule"]["mode"] == "real"
    assert rows(settings) == {body["schedule"]["schedule_id"]: ("SCHEDULED", "real")}


def test_create_refusals_are_400_and_write_nothing(client, settings):
    add_post(settings)
    (incoming(settings) / "lonely.jpg").write_bytes(JPEG)
    (incoming(settings) / "empty.jpg").write_bytes(JPEG)
    (incoming(settings) / "empty.txt").write_text("   ", encoding="utf-8")
    cases = [
        ("nothing-here", AT, "no post named"),
        ("lonely", AT, "waiting for its partner"),
        ("empty", AT, "is not valid"),
        ("launch", "2026-11-20 11:02", "too soon"),
        ("launch", "2028-01-01 09:00", "too far away"),
        ("launch", "tomorrow at nine", "Use the format"),
        ("launch", "2026-02-30 09:00", "not a real date"),
    ]
    for name, at, expected in cases:
        response = create(client, name, at)
        assert response.status_code == 400, (name, at, response.text)
        assert expected in response.json()["detail"], (name, at, response.json())
    assert not settings.app_db_path.exists() or rows(settings) == {}


def test_create_refuses_a_post_that_is_already_scheduled(client, settings):
    add_post(settings)
    created(client)
    response = create(client, at="2026-11-21 09:00")
    assert response.status_code == 400
    assert "already scheduled" in response.json()["detail"]
    assert len(rows(settings)) == 1


def test_messages_never_contain_absolute_paths(client, settings):
    response = create(client, "nothing-here")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert str(settings.base_dir) not in detail
    assert str(settings.data_dir) not in detail
    assert "LinkedIn" in detail  # Still says where it looked, relative to the data folder.


def test_create_needs_a_json_body(client, settings):
    add_post(settings)
    response = client.post("/api/schedules", data={"name": "launch", "publish_at": AT})
    assert response.status_code == 415
    assert not settings.app_db_path.exists()


# POST /api/schedules/{id}/cancel


def test_cancel_cancels_that_schedule_and_keeps_the_files(client, settings):
    add_post(settings)
    schedule_id = created(client)["schedule"]["schedule_id"]
    response = client.post(f"/api/schedules/{schedule_id}/cancel", json={})
    assert response.status_code == 200, response.text
    assert response.json()["schedule"]["status"] == "CANCELLED"
    assert rows(settings) == {schedule_id: ("CANCELLED", "simulated")}
    assert (incoming(settings) / "launch.jpg").exists() and (incoming(settings) / "launch.txt").exists()


def test_cancel_of_an_unknown_id_is_a_404(client, settings):
    response = client.post(f"/api/schedules/{uuid.uuid4()}/cancel", json={})
    assert response.status_code == 404
    add_post(settings)
    created(client)
    assert client.post(f"/api/schedules/{uuid.uuid4()}/cancel", json={}).status_code == 404


def test_cancel_of_a_finished_schedule_is_a_400(client, settings):
    add_post(settings)
    schedule_id = created(client)["schedule"]["schedule_id"]
    client.post(f"/api/schedules/{schedule_id}/cancel", json={})
    response = client.post(f"/api/schedules/{schedule_id}/cancel", json={})
    assert response.status_code == 400
    assert "CANCELLED" in response.json()["detail"]


def test_cancel_of_a_schedule_replaced_since_the_page_loaded_is_a_409(client, settings, monkeypatch):
    add_post(settings)
    old = created(client)["schedule"]["schedule_id"]
    stale = schedules_ops.find_schedule(settings, old)
    new = schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0).new.schedule_id
    # The page still shows the old schedule as active: the lookup sees it as it was when the page loaded.
    monkeypatch.setattr(schedules_ops, "find_schedule", lambda settings, schedule_id: stale)

    response = client.post(f"/api/schedules/{old}/cancel", json={})

    assert response.status_code == 409
    assert "Refresh and try again" in response.json()["detail"]
    assert rows(settings) == {old: ("CANCELLED", "simulated"), new: ("SCHEDULED", "simulated")}


# POST /api/schedules/{id}/reconfirm


def test_reconfirm_a_changed_post_keeps_its_time(client, settings):
    add_post(settings)
    old = created(client)["schedule"]["schedule_id"]
    (incoming(settings) / "launch.txt").write_text("Edited after scheduling.", encoding="utf-8")

    response = client.post(f"/api/schedules/{old}/reconfirm", json={})

    assert response.status_code == 201, response.text
    body = response.json()
    new = body["schedule"]["schedule_id"]
    assert body["replaces"] == old
    assert body["schedule"]["publish_at_local"] == "2026-11-20 12:00 Asia/Karachi"
    assert rows(settings) == {old: ("CANCELLED", "simulated"), new: ("SCHEDULED", "simulated")}
    assert [s["content_state"] for s in client.get("/api/schedules").json()["schedules"]] == ["ok"]


def test_reconfirm_with_a_new_time(client, settings):
    add_post(settings)
    old = created(client)["schedule"]["schedule_id"]
    response = client.post(f"/api/schedules/{old}/reconfirm", json={"publish_at": "2026-11-22 18:30"})
    assert response.status_code == 201, response.text
    assert response.json()["schedule"]["publish_at_local"] == "2026-11-22 18:30 Asia/Karachi"


def test_reconfirm_of_an_invalid_schedule_creates_a_new_one_and_keeps_the_old_invalid(client, settings):
    add_post(settings)
    old = created(client)["schedule"]["schedule_id"]
    conn = open_app_db(settings.app_db_path)
    try:
        ScheduleRepository(conn).mark_invalid(old, reason="content changed after scheduling")
    finally:
        conn.close()

    response = client.post(f"/api/schedules/{old}/reconfirm", json={})

    assert response.status_code == 201, response.text
    new = response.json()["schedule"]["schedule_id"]
    assert rows(settings) == {old: ("INVALID", "simulated"), new: ("SCHEDULED", "simulated")}


def test_reconfirm_real_needs_the_phrase_again_and_is_never_carried_over(client, settings):
    add_post(settings)
    old = created(client, real=True, confirm=PHRASE)["schedule"]["schedule_id"]

    refused = client.post(f"/api/schedules/{old}/reconfirm", json={"real": True})
    assert refused.status_code == 400
    assert PHRASE in refused.json()["detail"]
    assert rows(settings) == {old: ("SCHEDULED", "real")}  # The old schedule is untouched.

    simulated = client.post(f"/api/schedules/{old}/reconfirm", json={})
    assert simulated.status_code == 201, simulated.text
    assert simulated.json()["schedule"]["mode"] == "simulated"
    assert any("REAL" in w and "SIMULATED" in w for w in simulated.json()["warnings"])

    new = simulated.json()["schedule"]["schedule_id"]
    again = client.post(f"/api/schedules/{new}/reconfirm", json={"real": True, "confirm": PHRASE})
    assert again.status_code == 201, again.text
    assert again.json()["schedule"]["mode"] == "real"


def test_reconfirm_refusals(client, settings):
    add_post(settings)
    schedule_id = created(client)["schedule"]["schedule_id"]
    assert client.post(f"/api/schedules/{uuid.uuid4()}/reconfirm", json={}).status_code == 404
    bad_time = client.post(f"/api/schedules/{schedule_id}/reconfirm", json={"publish_at": "soon"})
    assert bad_time.status_code == 400
    client.post(f"/api/schedules/{schedule_id}/cancel", json={})
    finished = client.post(f"/api/schedules/{schedule_id}/reconfirm", json={})
    assert finished.status_code == 400
    assert "CANCELLED" in finished.json()["detail"]


def test_reconfirm_of_a_schedule_replaced_since_the_page_loaded_is_a_409(client, settings, monkeypatch):
    add_post(settings)
    old = created(client)["schedule"]["schedule_id"]
    stale = schedules_ops.find_schedule(settings, old)
    new = schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0).new.schedule_id
    monkeypatch.setattr(schedules_ops, "find_schedule", lambda settings, schedule_id: stale)

    response = client.post(f"/api/schedules/{old}/reconfirm", json={})

    assert response.status_code == 409
    assert rows(settings) == {old: ("CANCELLED", "simulated"), new: ("SCHEDULED", "simulated")}


# The run lock


def test_every_change_is_refused_with_409_while_a_run_holds_the_lock(client, settings):
    add_post(settings)
    schedule_id = created(client)["schedule"]["schedule_id"]
    add_post(settings, "other", caption="Another caption.")
    with single_run_lock(settings.lock_file):
        responses = [
            create(client, "other"),
            client.post(f"/api/schedules/{schedule_id}/cancel", json={}),
            client.post(f"/api/schedules/{schedule_id}/reconfirm", json={}),
        ]
        listed = client.get("/api/schedules")  # Reading never takes the lock.
    assert [r.status_code for r in responses] == [409, 409, 409]
    assert all("Another run is in progress" in r.json()["detail"] for r in responses)
    assert listed.status_code == 200
    assert rows(settings) == {schedule_id: ("SCHEDULED", "simulated")}


# GET /api/scan


def test_scan_shows_a_scheduled_post_and_a_schedule_whose_files_are_gone(client, settings):
    add_post(settings)
    add_post(settings, "gone", caption="Another caption.")
    launch = created(client)["schedule"]["schedule_id"]
    gone = created(client, "gone")["schedule"]["schedule_id"]
    (incoming(settings) / "gone.txt").unlink()

    body = client.get("/api/scan").json()

    candidate = next(c for c in body["candidates"] if c["name"] == "launch")
    assert candidate["schedule"] == {
        "schedule_id": launch,
        "mode": "simulated",
        "publish_at": "2026-11-20T07:00:00+00:00",
        "publish_at_local": "2026-11-20 12:00 Asia/Karachi",
        "content_state": "ok",
    }
    assert [(s["schedule_id"], s["content_state"]) for s in body["missing_schedules"]] == [(gone, "missing")]


def test_scan_of_an_unscheduled_post_has_no_schedule(client, settings):
    add_post(settings)
    body = client.get("/api/scan").json()
    assert body["candidates"][0]["schedule"] is None
    assert body["missing_schedules"] == []


# The token never comes back


def test_real_schedules_warn_about_the_token_but_never_return_it(client, settings, monkeypatch):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("LINKEDIN_TOKEN_CREATED", "2026-09-25")  # Expires 2026-11-24, before the second time.
    add_post(settings)

    first = create(client, real=True, confirm=PHRASE)
    assert first.status_code == 201, first.text
    assert first.json()["warnings"] == []
    schedule_id = first.json()["schedule"]["schedule_id"]
    later = client.post(
        f"/api/schedules/{schedule_id}/reconfirm", json={"publish_at": "2026-11-25 09:00", "real": True, "confirm": PHRASE}
    )
    assert later.status_code == 201, later.text
    assert any("expires on 2026-11-24" in w for w in later.json()["warnings"])

    responses = [
        first, later, client.get("/api/schedules"), client.get("/api/schedules?all=true"), client.get("/api/scan?real=true"),
        client.post(f"/api/schedules/{later.json()['schedule']['schedule_id']}/cancel", json={}),
    ]
    for response in responses:
        assert TOKEN not in response.text
        assert "access_token" not in response.text.lower()


def test_a_schedule_never_contains_hashes_or_paths(client, settings):
    add_post(settings)
    schedule = created(client)["schedule"]
    assert set(schedule) == {
        "schedule_id", "name", "mode", "status", "publish_at", "publish_at_local",
        "content_state", "reason", "job_id", "created_at",
    }
    listed = client.get("/api/schedules?all=true").text
    assert str(settings.base_dir) not in listed
    assert "sha256" not in listed


def test_listing_changes_nothing(client, settings):
    add_post(settings)
    schedule_id = created(client)["schedule"]["schedule_id"]
    before = settings.app_db_path.read_bytes()
    for query in ("", "?all=true", "?status=SCHEDULED"):
        assert client.get(f"/api/schedules{query}").status_code == 200
    assert settings.app_db_path.read_bytes() == before
    assert rows(settings) == {schedule_id: ("SCHEDULED", "simulated")}
