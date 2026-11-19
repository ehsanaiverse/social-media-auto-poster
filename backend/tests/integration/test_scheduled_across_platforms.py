"""D2: Instagram registered for simulated use, and run --scheduled across platforms (D49).

- One scheduled run carries out the oldest due schedule of any platform, at most one per run.
- A platform whose setup is refused is skipped: its schedules stay exactly as they were, the reason is shown
  and logged, the other platforms go on, and the run exits 3. If every platform with something to publish is
  refused, the whole run is refused as before (exit 1, nothing changed).
- Real Instagram is refused everywhere: a real run, a real schedule, a real re-confirmation.

Real Instagram schedules cannot be created (the schedule command refuses them), so the skip tests write one
straight into the job database, standing for a row that could only come from a future version.
"""

import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings, ensure_data_dirs
from social_media_auto_poster.models import Platform, PublishMode, PublishResult
from social_media_auto_poster.operations import clock as clock_module
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations.run import run_once
from social_media_auto_poster.operations.schedules import reconfirm_post, schedule_post
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.scheduling import ScheduleError
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.schedules import ScheduleRepository

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
SCHEDULED_AT = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)
DUE = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)  # "2026-12-01 09:00" in Karachi.
D49 = "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set in backend\\.env"  # D48: without credentials in backend\.env, real Instagram is refused.


class FakePublisher:
    def __init__(self):
        self.published: list[str] = []

    def publish(self, post, /):
        self.published.append(post.candidate.stem)
        return PublishResult(success=True, simulated=False, platform_post_id=f"urn:li:share:{len(self.published)}")


@pytest.fixture
def clock(monkeypatch) -> dict:
    now = {"now": SCHEDULED_AT}
    monkeypatch.setattr(clock_module, "now", lambda: now["now"])
    return now


@pytest.fixture
def base(tmp_path, monkeypatch, clock) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    monkeypatch.delenv("AUTOPOSTER_TIMEZONE", raising=False)
    monkeypatch.setattr(linkedin_ops, "_linkedin_client", lambda token: pytest.fail("LinkedIn must not be contacted"))
    return tmp_path


@pytest.fixture
def settings(base) -> Settings:
    settings = Settings(base_dir=base, data_dir=base / "data")
    ensure_data_dirs(settings)
    return settings


@pytest.fixture
def linkedin(monkeypatch) -> FakePublisher:
    fake = FakePublisher()
    monkeypatch.setattr(linkedin_ops, "_real_publisher", lambda settings: fake)
    return fake


def add_post(settings: Settings, platform: Platform, stem: str) -> None:
    folder = settings.incoming_dir(platform)
    (folder / f"{stem}.jpg").write_bytes(JPEG)
    (folder / f"{stem}.txt").write_bytes(f"Hello from {stem}".encode())


def schedule(settings, platform: Platform, stem: str, at: str = "2026-12-01 09:00", *, real: bool = False) -> None:
    schedule_post(settings, stem, at, real=real, interval_s=0, platform=platform)


def insert_real_schedule(settings, platform: Platform, stem: str, publish_at: datetime) -> None:
    """A real schedule row written directly: the schedule command refuses real Instagram (D49)."""
    folder = settings.incoming_dir(platform)
    conn = open_app_db(settings.app_db_path)
    try:
        ScheduleRepository(conn).create(
            schedule_id=str(uuid.uuid4()), platform=platform, mode=PublishMode.REAL, stem=stem,
            media_name=f"{stem}.jpg", caption_name=f"{stem}.txt",
            media_sha256=sha256_file(folder / f"{stem}.jpg"), caption_sha256=sha256_file(folder / f"{stem}.txt"),
            publish_at=publish_at,
        )
    finally:
        conn.close()


def run(*args: str) -> int:
    return cli.main(["run", "--interval", "0", *args])


def query(settings: Settings, sql: str) -> list[tuple]:
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def jobs(settings: Settings) -> list[tuple]:
    return query(settings, "SELECT platform, stem, mode, status FROM jobs ORDER BY created_at, rowid")


def states(settings: Settings) -> dict[tuple, str]:
    return {(platform, stem): status for platform, stem, status in
            query(settings, "SELECT platform, stem, status FROM schedules")}


# Simulated Instagram, and the oldest due schedule across platforms


def test_a_simulated_instagram_run_works_and_says_nothing_was_sent_to_instagram(settings):
    add_post(settings, Platform.INSTAGRAM, "launch")

    report = run_once(settings, PublishMode.SIMULATED, interval_s=0, platform=Platform.INSTAGRAM)

    assert report.exit_code == 0 and jobs(settings) == [("Instagram", "launch", "simulated", "SIMULATED")]
    assert report.footer == "\nSimulation mode: nothing was sent to Instagram in this run."
    assert (settings.incoming_dir(Platform.INSTAGRAM) / "launch.jpg").exists()  # Simulated: files stay.


def test_the_oldest_due_schedule_of_any_platform_goes_first_one_per_run(settings, clock, caplog):
    add_post(settings, Platform.LINKEDIN, "li-post")
    add_post(settings, Platform.INSTAGRAM, "ig-post")
    schedule(settings, Platform.LINKEDIN, "li-post", "2026-12-01 09:00")
    schedule(settings, Platform.INSTAGRAM, "ig-post", "2026-12-01 08:30")  # Older: goes first.
    clock["now"] = DUE + timedelta(minutes=5)

    with caplog.at_level(logging.INFO):
        assert run("--scheduled") == 0
    assert jobs(settings) == [("Instagram", "ig-post", "simulated", "SIMULATED")]
    assert states(settings) == {("LinkedIn", "li-post"): "SCHEDULED", ("Instagram", "ig-post"): "DONE"}
    assert any("Run started: platform=LinkedIn,Instagram mode=simulated scheduled=yes" in r.message
               for r in caplog.records)

    assert run("--scheduled") == 0
    assert [job[:2] for job in jobs(settings)] == [("Instagram", "ig-post"), ("LinkedIn", "li-post")]


# Real scheduled runs: a refused platform is skipped


def test_a_refused_platform_is_skipped_and_the_others_go_on_with_exit_3(settings, clock, linkedin, capsys, caplog):
    add_post(settings, Platform.LINKEDIN, "li-post")
    add_post(settings, Platform.INSTAGRAM, "ig-post")
    schedule(settings, Platform.LINKEDIN, "li-post", "2026-12-01 09:00", real=True)
    insert_real_schedule(settings, Platform.INSTAGRAM, "ig-post", DUE - timedelta(minutes=30))  # Older.
    clock["now"] = DUE + timedelta(minutes=5)

    with caplog.at_level(logging.WARNING):
        assert run("--real", "--scheduled") == 3

    out = capsys.readouterr().out
    assert linkedin.published == ["li-post"]
    assert states(settings) == {("LinkedIn", "li-post"): "DONE", ("Instagram", "ig-post"): "SCHEDULED"}
    assert jobs(settings) == [("LinkedIn", "li-post", "real", "PUBLISHED")]
    assert "SKIPPED (1) - platforms refused this run; their schedules are unchanged" in out
    assert D49 in out
    assert any("Platform skipped by the scheduled run: platform=Instagram reason=token" in r.message
               for r in caplog.records)


def test_a_skipped_platforms_missed_schedule_is_not_changed(settings, clock, linkedin):
    add_post(settings, Platform.LINKEDIN, "li-late")
    add_post(settings, Platform.LINKEDIN, "li-due")
    add_post(settings, Platform.INSTAGRAM, "ig-late")
    add_post(settings, Platform.INSTAGRAM, "ig-due")
    schedule(settings, Platform.LINKEDIN, "li-late", "2026-12-01 09:00", real=True)
    schedule(settings, Platform.LINKEDIN, "li-due", "2026-12-01 16:00", real=True)
    insert_real_schedule(settings, Platform.INSTAGRAM, "ig-late", DUE)
    insert_real_schedule(settings, Platform.INSTAGRAM, "ig-due", DUE + timedelta(hours=6, minutes=30))
    clock["now"] = DUE + timedelta(hours=7)  # 09:00 is more than 6 hours late; 16:00-ish is due and in time.

    assert run("--real", "--scheduled") == 3

    assert states(settings) == {
        ("LinkedIn", "li-late"): "MISSED", ("LinkedIn", "li-due"): "DONE",
        ("Instagram", "ig-late"): "SCHEDULED", ("Instagram", "ig-due"): "SCHEDULED",  # Skipped: untouched.
    }


def test_when_every_platform_with_something_due_is_refused_nothing_changes_and_the_exit_is_1(
    settings, base, clock, capsys
):
    (base / ".env").write_text("LINKEDIN_ACCESS_TOKEN=AQVtesttoken\nLINKEDIN_TOKEN_CREATED=2020-01-01\n",
                               encoding="utf-8")
    add_post(settings, Platform.LINKEDIN, "li-post")
    add_post(settings, Platform.INSTAGRAM, "ig-post")
    schedule(settings, Platform.LINKEDIN, "li-post", "2026-12-01 09:00", real=True)
    insert_real_schedule(settings, Platform.INSTAGRAM, "ig-post", DUE - timedelta(minutes=30))
    clock["now"] = DUE + timedelta(minutes=5)

    assert run("--real", "--scheduled") == 1

    err = capsys.readouterr().err
    assert D49 in err and "expired" in err
    assert set(states(settings).values()) == {"SCHEDULED"} and jobs(settings) == []


# Real Instagram without credentials (D48)


def test_a_real_instagram_run_without_credentials_is_refused(settings, linkedin):
    add_post(settings, Platform.INSTAGRAM, "launch")

    report = run_once(settings, PublishMode.REAL, interval_s=0, platform=Platform.INSTAGRAM)

    assert report.exit_code == 1 and report.refusal.kind == "token" and D49 in report.refusal.text
    # As for LinkedIn, the token check comes after the job database is opened; nothing is published or moved.
    assert linkedin.published == [] and (settings.incoming_dir(Platform.INSTAGRAM) / "launch.jpg").is_file()


def test_a_real_instagram_schedule_is_accepted_as_a_real_one(settings):
    add_post(settings, Platform.INSTAGRAM, "launch")

    schedule(settings, Platform.INSTAGRAM, "launch", real=True)  # D48: --real is the confirmation (D46).

    assert states(settings) == {("Instagram", "launch"): "SCHEDULED"}


def test_a_real_reconfirmation_of_an_instagram_schedule_replaces_it(settings):
    add_post(settings, Platform.INSTAGRAM, "launch")
    schedule(settings, Platform.INSTAGRAM, "launch")  # Simulated first.

    reconfirm_post(settings, "launch", real=True, interval_s=0, platform=Platform.INSTAGRAM)

    assert states(settings) == {("Instagram", "launch"): "SCHEDULED"}
    assert ScheduleError  # Still imported by this file's other tests.
