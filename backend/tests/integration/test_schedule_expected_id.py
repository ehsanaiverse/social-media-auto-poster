"""``expected_schedule_id`` on ``unschedule_post`` and ``reconfirm_post`` (used by the API).

The API acts on a schedule ID, the operations on a post name. The ID is checked again inside the run lock,
so a schedule replaced in between (for example by a CLI ``reconfirm``) is never cancelled or re-confirmed by
mistake. Without the parameter (the CLI) nothing changes.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_media_auto_poster.config import Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.models import Platform, ScheduleStatus
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations import schedules as schedules_ops
from social_media_auto_poster.services.scheduling import ScheduleError
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.schedules import ScheduleRepository

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
NOW = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)  # 11:00 in Asia/Karachi.


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setattr(clock, "now", lambda: NOW)
    base = tmp_path / "base"
    base.mkdir()
    settings = load_settings(base_dir=base, environ={})
    ensure_data_dirs(settings)
    return settings


def add_post(settings: Settings, stem: str = "launch", caption: str = "A caption.") -> None:
    folder = settings.incoming_dir(Platform.LINKEDIN)
    (folder / f"{stem}.jpg").write_bytes(JPEG)
    (folder / f"{stem}.txt").write_text(caption, encoding="utf-8")


def statuses(settings: Settings) -> dict[str, ScheduleStatus]:
    conn = open_app_db(settings.app_db_path)
    try:
        return {r.schedule_id: r.status for r in ScheduleRepository(conn).list_recent(50)}
    finally:
        conn.close()


def schedule(settings: Settings, stem: str = "launch", at: str = "2026-11-20 12:00") -> str:
    return schedules_ops.schedule_post(settings, stem, at, real=False, interval_s=0).schedule.schedule_id


def test_unschedule_with_the_matching_id_cancels_it(settings):
    add_post(settings)
    schedule_id = schedule(settings)
    cancelled = schedules_ops.unschedule_post(settings, "launch", expected_schedule_id=schedule_id)
    assert cancelled.schedule_id == schedule_id
    assert statuses(settings) == {schedule_id: ScheduleStatus.CANCELLED}


def test_unschedule_with_another_id_changes_nothing(settings):
    add_post(settings)
    schedule_id = schedule(settings)
    with pytest.raises(ScheduleError) as caught:
        schedules_ops.unschedule_post(settings, "launch", expected_schedule_id=str(uuid.uuid4()))
    assert caught.value.kind == "schedule_changed"
    assert statuses(settings) == {schedule_id: ScheduleStatus.SCHEDULED}


def test_unschedule_with_an_id_after_the_schedule_was_replaced_changes_nothing(settings):
    add_post(settings)
    old = schedule(settings)
    new = schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0).new.schedule_id
    with pytest.raises(ScheduleError) as caught:
        schedules_ops.unschedule_post(settings, "launch", expected_schedule_id=old)
    assert caught.value.kind == "schedule_changed"
    assert statuses(settings) == {old: ScheduleStatus.CANCELLED, new: ScheduleStatus.SCHEDULED}


def test_unschedule_with_an_id_when_nothing_is_active_is_schedule_changed(settings):
    add_post(settings)
    schedule_id = schedule(settings)
    schedules_ops.unschedule_post(settings, "launch")
    with pytest.raises(ScheduleError) as caught:
        schedules_ops.unschedule_post(settings, "launch", expected_schedule_id=schedule_id)
    assert caught.value.kind == "schedule_changed"


def test_unschedule_without_an_id_is_unchanged(settings):
    add_post(settings)
    schedule_id = schedule(settings)
    assert schedules_ops.unschedule_post(settings, "launch").schedule_id == schedule_id


def test_reconfirm_with_the_matching_id_replaces_it(settings):
    add_post(settings)
    old = schedule(settings)
    result = schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0, expected_schedule_id=old)
    assert result.old.schedule_id == old
    assert statuses(settings) == {old: ScheduleStatus.CANCELLED, result.new.schedule_id: ScheduleStatus.SCHEDULED}


def test_reconfirm_with_a_stale_id_changes_nothing(settings):
    add_post(settings)
    old = schedule(settings)
    new = schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0).new.schedule_id
    with pytest.raises(ScheduleError) as caught:
        schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0, expected_schedule_id=old)
    assert caught.value.kind == "schedule_changed"
    assert statuses(settings) == {old: ScheduleStatus.CANCELLED, new: ScheduleStatus.SCHEDULED}


def test_reconfirm_with_an_id_still_refuses_a_post_with_nothing_to_reconfirm(settings):
    add_post(settings)
    schedule_id = schedule(settings)
    schedules_ops.unschedule_post(settings, "launch")
    with pytest.raises(ScheduleError) as caught:
        schedules_ops.reconfirm_post(settings, "launch", real=False, interval_s=0, expected_schedule_id=schedule_id)
    assert caught.value.kind == "nothing_to_reconfirm"


def test_find_schedule_reads_one_schedule_without_creating_a_database(settings):
    assert schedules_ops.find_schedule(settings, str(uuid.uuid4())) is None
    assert not settings.app_db_path.exists()
    add_post(settings)
    schedule_id = schedule(settings)
    found = schedules_ops.find_schedule(settings, schedule_id)
    assert found is not None and found.schedule_id == schedule_id and found.stem == "launch"
    assert schedules_ops.find_schedule(settings, str(uuid.uuid4())) is None
