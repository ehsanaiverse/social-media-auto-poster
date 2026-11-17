"""Tests for schedule times and due selection (D46). Every test passes "now"; none reads the real clock."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from social_media_auto_poster.models import Platform, PublishMode, ScheduleStatus
from social_media_auto_poster.services.scheduling import (
    GRACE_WINDOW,
    MAX_LEAD,
    MAX_SCHEDULED_PER_RUN,
    MIN_LEAD,
    ScheduleTimeError,
    parse_publish_at,
    select_due,
)
from social_media_auto_poster.storage.schedules import ScheduleRecord

KARACHI = ZoneInfo("Asia/Karachi")
LONDON = ZoneInfo("Europe/London")  # Has daylight saving time, unlike Karachi.
NOW = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)  # 12:00 in Karachi.


def test_the_agreed_limits():
    assert MIN_LEAD == timedelta(minutes=5)
    assert MAX_LEAD == timedelta(days=365)
    assert GRACE_WINDOW == timedelta(hours=6)
    assert MAX_SCHEDULED_PER_RUN == 1


# Parsing "YYYY-MM-DD HH:MM" in the configured zone


def test_a_karachi_time_is_stored_in_utc():
    assert parse_publish_at("2026-12-01 09:00", zone=KARACHI, now=NOW) == datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


def test_the_configured_zone_is_used_including_its_summer_time():
    now = datetime(2026, 11, 17, tzinfo=UTC)
    assert parse_publish_at("2026-12-01 09:00", zone=LONDON, now=now) == datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    assert parse_publish_at("2027-07-01 09:00", zone=LONDON, now=now) == datetime(2027, 7, 1, 8, 0, tzinfo=UTC)


def test_surrounding_spaces_are_ignored():
    assert parse_publish_at("  2026-12-01 09:00 ", zone=KARACHI, now=NOW) == datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


@pytest.mark.parametrize("text", [
    "", "tomorrow", "2026-12-01", "2026-12-01 09", "2026-12-01T09:00", "01-12-2026 09:00", "2026/12/01 09:00",
    "2026-12-01 9:00", "2026-12-01 09:00:00", "2026-13-01 09:00", "2026-02-30 09:00", "2026-12-01 24:00",
    "2026-12-01 09:60", "２０２６-12-01 09:00",
])
def test_a_badly_formatted_time_is_refused(text):
    with pytest.raises(ScheduleTimeError, match="YYYY-MM-DD HH:MM"):
        parse_publish_at(text, zone=KARACHI, now=NOW)


def test_a_time_less_than_5_minutes_ahead_is_refused():
    with pytest.raises(ScheduleTimeError, match="at least 5 minutes ahead"):
        parse_publish_at("2026-11-17 12:04", zone=KARACHI, now=NOW)


def test_a_time_in_the_past_is_refused():
    with pytest.raises(ScheduleTimeError, match="at least 5 minutes ahead"):
        parse_publish_at("2026-11-16 12:00", zone=KARACHI, now=NOW)


def test_exactly_5_minutes_ahead_is_accepted():
    assert parse_publish_at("2026-11-17 12:05", zone=KARACHI, now=NOW) == NOW + timedelta(minutes=5)


def test_more_than_a_year_ahead_is_refused():
    with pytest.raises(ScheduleTimeError, match="at most 1 year ahead"):
        parse_publish_at("2027-11-17 12:01", zone=KARACHI, now=NOW)


def test_exactly_a_year_ahead_is_accepted():
    assert parse_publish_at("2027-11-17 12:00", zone=KARACHI, now=NOW) == NOW + timedelta(days=365)


def test_a_local_time_skipped_when_clocks_go_forward_is_refused():
    # Europe/London jumps from 01:00 to 02:00 on 2027-03-28: 01:30 never happens that night.
    with pytest.raises(ScheduleTimeError, match="does not exist"):
        parse_publish_at("2027-03-28 01:30", zone=LONDON, now=datetime(2027, 1, 1, tzinfo=UTC))


def test_a_local_time_that_happens_twice_when_clocks_go_back_is_refused():
    # Europe/London goes back from 02:00 to 01:00 on 2027-10-31: 01:30 happens twice that night.
    with pytest.raises(ScheduleTimeError, match="happens twice"):
        parse_publish_at("2027-10-31 01:30", zone=LONDON, now=datetime(2027, 1, 1, tzinfo=UTC))


def test_times_just_outside_a_clock_change_are_accepted():
    now = datetime(2027, 1, 1, tzinfo=UTC)
    assert parse_publish_at("2027-03-28 00:59", zone=LONDON, now=now) == datetime(2027, 3, 28, 0, 59, tzinfo=UTC)
    assert parse_publish_at("2027-03-28 02:00", zone=LONDON, now=now) == datetime(2027, 3, 28, 1, 0, tzinfo=UTC)
    assert parse_publish_at("2027-10-31 02:00", zone=LONDON, now=now) == datetime(2027, 10, 31, 2, 0, tzinfo=UTC)


def test_now_must_carry_a_time_zone():
    with pytest.raises(ValueError, match="time zone"):
        parse_publish_at("2026-12-01 09:00", zone=KARACHI, now=datetime(2026, 11, 17, 7, 0))


# Choosing what is due


def schedule(stem: str, publish_at: datetime, *, mode=PublishMode.REAL, status=ScheduleStatus.SCHEDULED,
             created_at: datetime = NOW - timedelta(days=1)) -> ScheduleRecord:
    return ScheduleRecord(
        schedule_id=str(uuid.uuid4()), platform=Platform.LINKEDIN, mode=mode, stem=stem, media_name=f"{stem}.jpg",
        caption_name=f"{stem}.txt", media_sha256="m" * 64, caption_sha256="c" * 64, publish_at=publish_at,
        status=status, job_id=None, reason=None, created_at=created_at, updated_at=created_at,
    )


def stems(items) -> list[str]:
    return [getattr(item, "schedule", item).stem for item in items]


def test_nothing_due_selects_nothing():
    selection = select_due([schedule("later", NOW + timedelta(minutes=1))], mode=PublishMode.REAL, now=NOW)

    assert selection.publish == () and selection.missed == () and selection.waiting == ()


def test_at_most_one_is_published_per_run_oldest_first():
    rows = [schedule("newer", NOW - timedelta(minutes=10)), schedule("oldest", NOW - timedelta(minutes=30)),
            schedule("on-time", NOW)]

    selection = select_due(rows, mode=PublishMode.REAL, now=NOW)

    assert stems(selection.publish) == ["oldest"]
    assert stems(selection.waiting) == ["newer", "on-time"]  # Stay SCHEDULED for the next runs.
    assert selection.missed == ()


def test_equal_times_are_taken_in_the_order_they_were_scheduled():
    rows = [schedule("second", NOW, created_at=NOW - timedelta(hours=1)),
            schedule("first", NOW, created_at=NOW - timedelta(hours=2))]

    assert stems(select_due(rows, mode=PublishMode.REAL, now=NOW).publish) == ["first"]


def test_only_schedules_of_the_run_mode_are_considered():
    rows = [schedule("simulated", NOW - timedelta(hours=1), mode=PublishMode.SIMULATED),
            schedule("real", NOW - timedelta(minutes=5))]

    assert stems(select_due(rows, mode=PublishMode.REAL, now=NOW).publish) == ["real"]
    assert stems(select_due(rows, mode=PublishMode.SIMULATED, now=NOW).publish) == ["simulated"]


def test_only_scheduled_rows_are_considered():
    rows = [schedule(status.value, NOW - timedelta(hours=1), status=status)
            for status in ScheduleStatus if status is not ScheduleStatus.SCHEDULED]

    selection = select_due(rows, mode=PublishMode.REAL, now=NOW)

    assert selection.publish == () and selection.missed == () and selection.waiting == ()


def test_how_late_a_due_post_is_is_reported():
    on_time = select_due([schedule("on-time", NOW)], mode=PublishMode.REAL, now=NOW).publish[0]
    late = select_due([schedule("late", NOW - timedelta(minutes=90))], mode=PublishMode.REAL, now=NOW).publish[0]

    assert on_time.late_by == timedelta(0)
    assert late.late_by == timedelta(minutes=90)


def test_exactly_6_hours_late_is_still_published():
    selection = select_due([schedule("late", NOW - timedelta(hours=6))], mode=PublishMode.REAL, now=NOW)

    assert stems(selection.publish) == ["late"]
    assert selection.publish[0].late_by == timedelta(hours=6)
    assert selection.missed == ()


def test_more_than_6_hours_late_is_missed_and_never_published():
    selection = select_due([schedule("too-late", NOW - timedelta(hours=6, seconds=1))], mode=PublishMode.REAL, now=NOW)

    assert selection.publish == ()
    assert stems(selection.missed) == ["too-late"]


def test_missed_schedules_do_not_use_up_the_one_post_per_run():
    rows = [schedule("too-late-1", NOW - timedelta(hours=9)), schedule("too-late-2", NOW - timedelta(hours=7)),
            schedule("in-time", NOW - timedelta(hours=1)), schedule("next", NOW - timedelta(minutes=1))]

    selection = select_due(rows, mode=PublishMode.REAL, now=NOW)

    assert stems(selection.missed) == ["too-late-1", "too-late-2"]  # All of them, oldest first.
    assert stems(selection.publish) == ["in-time"]
    assert stems(selection.waiting) == ["next"]


def test_select_due_needs_an_aware_now():
    with pytest.raises(ValueError, match="time zone"):
        select_due([], mode=PublishMode.REAL, now=datetime(2026, 11, 17, 7, 0))
