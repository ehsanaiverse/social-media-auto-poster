"""Tests for the schedules table and ScheduleRepository (D46). Temporary databases only."""

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from social_media_auto_poster.models import JobStatus, Platform, PublishMode, ScheduleStatus
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository
from social_media_auto_poster.storage.schedules import (
    DuplicateScheduleError,
    ScheduleNotFoundError,
    ScheduleRecord,
    ScheduleRepository,
)

NOW = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    connection = open_app_db(tmp_path / "app.db")
    yield connection
    connection.close()


@pytest.fixture
def schedules(conn) -> ScheduleRepository:
    return ScheduleRepository(conn, now=lambda: NOW)


def add(schedules: ScheduleRepository, stem="launch", *, publish_at=NOW + timedelta(hours=1),
        mode=PublishMode.REAL, platform=Platform.LINKEDIN, media="m" * 64, caption="c" * 64) -> ScheduleRecord:
    return schedules.create(
        schedule_id=str(uuid.uuid4()),
        platform=platform,
        mode=mode,
        stem=stem,
        media_name=f"{stem}.jpg",
        caption_name=f"{stem}.txt",
        media_sha256=media,
        caption_sha256=caption,
        publish_at=publish_at,
    )


def make_job(conn) -> str:
    job_id = str(uuid.uuid4())
    JobRepository(conn).create(
        job_id=job_id, platform=Platform.LINKEDIN, mode=PublishMode.REAL, stem="launch", media_name="launch.jpg",
        caption_name="launch.txt", media_sha256="m" * 64, caption_sha256="c" * 64, status=JobStatus.PUBLISHING,
    )
    return job_id


# Creating and reading


def test_create_and_get_round_trip_with_publish_at_in_utc(schedules):
    karachi = timezone(timedelta(hours=5))
    created = add(schedules, publish_at=datetime(2026, 12, 1, 9, 0, tzinfo=karachi))

    assert created.publish_at == datetime(2026, 12, 1, 4, 0, tzinfo=UTC)
    assert created.status is ScheduleStatus.SCHEDULED
    assert created.mode is PublishMode.REAL and created.platform is Platform.LINKEDIN
    assert created.job_id is None and created.reason is None
    assert created.created_at == created.updated_at == NOW
    assert schedules.get(created.schedule_id) == created


def test_a_naive_publish_time_is_refused(schedules):
    with pytest.raises(ValueError, match="time zone"):
        add(schedules, publish_at=datetime(2026, 12, 1, 9, 0))


def test_get_of_an_unknown_schedule_is_none(schedules):
    assert schedules.get("no-such-id") is None


# One active schedule per post


def test_only_one_active_schedule_per_post_name(schedules):
    add(schedules, "launch")

    with pytest.raises(DuplicateScheduleError, match="launch"):
        add(schedules, "launch")


@pytest.mark.parametrize(("first", "second"), [("Launch", "launch"), ("ÄRGER", "ärger"), ("STRASSE", "straße")])
def test_post_names_are_compared_without_case(schedules, first, second):
    add(schedules, first)

    with pytest.raises(DuplicateScheduleError):
        add(schedules, second)


def test_find_active_ignores_case(schedules):
    created = add(schedules, "Launch")

    assert schedules.find_active(platform=Platform.LINKEDIN, stem="LAUNCH") == created
    assert schedules.find_active(platform=Platform.LINKEDIN, stem="other") is None


def test_post_names_are_casefolded_not_just_lowercased(schedules, conn):
    # "straße".lower() stays "straße"; only casefold() makes it "strasse", the same as "STRASSE".
    created = add(schedules, "straße")

    assert conn.execute("SELECT stem_key FROM schedules").fetchone()[0] == "strasse"
    assert schedules.find_active(platform=Platform.LINKEDIN, stem="STRASSE") == created
    assert created.stem == "straße"  # The name itself is kept as written.


def test_another_platform_may_schedule_the_same_name(schedules):
    add(schedules, "launch", platform=Platform.LINKEDIN)
    add(schedules, "launch", platform=Platform.FACEBOOK)


@pytest.mark.parametrize("finish", ["cancel", "invalid", "missed", "done"])
def test_a_finished_schedule_does_not_block_a_new_one(schedules, conn, finish):
    first = add(schedules, "launch")
    if finish == "cancel":
        schedules.cancel(first.schedule_id)
    elif finish == "invalid":
        schedules.mark_invalid(first.schedule_id, reason="the image changed")
    elif finish == "missed":
        schedules.mark_missed(first.schedule_id, reason="more than 6 hours late")
    else:
        schedules.mark_done(first.schedule_id, job_id=make_job(conn))

    second = add(schedules, "launch")

    assert second.status is ScheduleStatus.SCHEDULED
    assert schedules.find_active(platform=Platform.LINKEDIN, stem="launch") == second


# Status changes


def test_mark_done_records_the_job(schedules, conn):
    created = add(schedules)
    job_id = make_job(conn)

    done = schedules.mark_done(created.schedule_id, job_id=job_id)

    assert done.status is ScheduleStatus.DONE and done.job_id == job_id


@pytest.mark.parametrize(("change", "status"), [("mark_invalid", ScheduleStatus.INVALID),
                                                ("mark_missed", ScheduleStatus.MISSED)])
def test_invalid_and_missed_keep_their_reason(schedules, change, status):
    created = add(schedules)

    changed = getattr(schedules, change)(created.schedule_id, reason="why it stopped")

    assert changed.status is status and changed.reason == "why it stopped" and changed.job_id is None


def test_cancel_sets_cancelled(schedules):
    created = add(schedules)

    assert schedules.cancel(created.schedule_id).status is ScheduleStatus.CANCELLED


@pytest.mark.parametrize("change", ["cancel", "mark_invalid", "mark_missed", "mark_done"])
def test_only_a_scheduled_schedule_can_change(schedules, conn, change):
    created = add(schedules)
    schedules.cancel(created.schedule_id)
    arguments = {"cancel": {}, "mark_invalid": {"reason": "x"}, "mark_missed": {"reason": "x"},
                 "mark_done": {"job_id": make_job(conn)}}[change]

    with pytest.raises(ScheduleNotFoundError):
        getattr(schedules, change)(created.schedule_id, **arguments)

    assert schedules.get(created.schedule_id).status is ScheduleStatus.CANCELLED


def test_changing_an_unknown_schedule_is_refused(schedules):
    with pytest.raises(ScheduleNotFoundError):
        schedules.cancel("no-such-id")


# Rules the database enforces itself


def test_done_without_a_job_id_is_refused_by_the_database(schedules, conn):
    created = add(schedules)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute("UPDATE schedules SET status = 'DONE' WHERE schedule_id = ?", (created.schedule_id,))


def test_done_with_an_unknown_job_is_refused_by_the_database(schedules):
    created = add(schedules)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        schedules.mark_done(created.schedule_id, job_id="no-such-job")
    assert schedules.get(created.schedule_id).status is ScheduleStatus.SCHEDULED


def test_foreign_keys_are_enforced_on_every_connection_open_app_db_returns(tmp_path):
    path = tmp_path / "app.db"
    first = open_app_db(path)
    schedule_id = add(ScheduleRepository(first)).schedule_id
    first.close()

    # PRAGMA foreign_keys belongs to a connection, not to the file: a plain connection does not enforce it...
    plain = sqlite3.connect(path)
    assert plain.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    plain.close()

    # ...but every connection from open_app_db does, so a DONE schedule must point at a real job.
    reopened = open_app_db(path)
    assert reopened.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        ScheduleRepository(reopened).mark_done(schedule_id, job_id="no-such-job")
    assert ScheduleRepository(reopened).get(schedule_id).status is ScheduleStatus.SCHEDULED
    reopened.close()


def test_an_unknown_status_or_mode_is_refused_by_the_database(schedules, conn):
    created = add(schedules)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute("UPDATE schedules SET status = 'LATER' WHERE schedule_id = ?", (created.schedule_id,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute("UPDATE schedules SET mode = 'maybe' WHERE schedule_id = ?", (created.schedule_id,))


def test_status_check_matches_the_schedule_status_enum(conn):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'schedules'").fetchone()[0]

    for status in ScheduleStatus:
        assert f"'{status.value}'" in sql


# Due schedules and listing


def test_due_returns_only_scheduled_rows_of_the_mode_that_are_due_oldest_first(schedules):
    late = add(schedules, "late", publish_at=NOW - timedelta(hours=2))
    on_time = add(schedules, "on-time", publish_at=NOW)
    add(schedules, "future", publish_at=NOW + timedelta(minutes=1))
    add(schedules, "simulated", publish_at=NOW - timedelta(hours=3), mode=PublishMode.SIMULATED)
    add(schedules, "other-platform", publish_at=NOW - timedelta(hours=3), platform=Platform.FACEBOOK)
    cancelled = add(schedules, "cancelled", publish_at=NOW - timedelta(hours=4))
    schedules.cancel(cancelled.schedule_id)

    due = schedules.due(platform=Platform.LINKEDIN, mode=PublishMode.REAL, now=NOW)

    assert [s.stem for s in due] == [late.stem, on_time.stem]


def test_list_recent_is_newest_first_and_can_filter_by_status(conn):
    times = iter([NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2), NOW + timedelta(minutes=3)])
    schedules = ScheduleRepository(conn, now=lambda: next(times))
    first = add(schedules, "first")
    second = add(schedules, "second")
    schedules.cancel(first.schedule_id)

    assert [s.stem for s in schedules.list_recent()] == ["second", "first"]
    assert [s.stem for s in schedules.list_recent(status=ScheduleStatus.CANCELLED)] == ["first"]
    assert schedules.list_recent(limit=1) == [schedules.get(second.schedule_id)]
