"""The schedule, unschedule and schedules commands, and runs skipping scheduled posts (D46, commit 4).

Every test has its own base folder and data folder. The clock is fixed (``main._now``); no test reads the
real time. No test reaches LinkedIn: the network is blocked (conftest) and no LinkedIn client is created.
"""

import re
import sqlite3
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.operations import clock
from social_media_auto_poster.config import Settings, ensure_data_dirs
from social_media_auto_poster.models import Platform, PublishMode, PublishResult, ScheduleStatus
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage import database
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.schedules import ScheduleRepository

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
PNG = (IMAGES / "image-6x3.png").read_bytes()
NOW = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)  # 12:00 in Karachi.
LINKEDIN = Platform.LINKEDIN


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    monkeypatch.delenv("AUTOPOSTER_TIMEZONE", raising=False)
    monkeypatch.setattr(clock, "now", lambda: NOW)
    monkeypatch.setattr(cli, "_linkedin_client", _no_linkedin)
    return tmp_path


@pytest.fixture
def settings(base) -> Settings:
    settings = Settings(base_dir=base, data_dir=base / "data")
    ensure_data_dirs(settings)
    return settings


def _no_linkedin(token):
    raise AssertionError("LinkedIn must not be contacted in this test")


def incoming(settings: Settings) -> Path:
    return settings.incoming_dir(LINKEDIN)


def add_post(settings: Settings, stem: str, *, image: bytes = JPEG, ext: str = ".jpg", caption: bytes | None = None):
    (incoming(settings) / f"{stem}{ext}").write_bytes(image)
    (incoming(settings) / f"{stem}.txt").write_bytes(caption if caption is not None else f"Hello from {stem}".encode())


def schedule(*args: str) -> int:
    return cli.main(["schedule", *args, "--interval", "0"])


def rows(settings: Settings) -> list[tuple]:
    if not settings.app_db_path.exists():
        return []
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute(
            "SELECT stem, mode, status, publish_at, media_sha256, caption_sha256 FROM schedules ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


def job_rows(settings: Settings) -> list[tuple]:
    if not settings.app_db_path.exists():
        return []
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute("SELECT stem, mode, status FROM jobs ORDER BY created_at, rowid").fetchall()
    finally:
        conn.close()


def huge_png() -> bytes:
    """The real PNG fixture with its IHDR size changed to 10000 x 10000 (over LinkedIn's pixel limit)."""
    return PNG[:16] + struct.pack(">II", 10000, 10000) + PNG[24:]


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


class FakePublisher:
    """Stands in for the real publisher: records what it was asked to publish."""

    def __init__(self):
        self.published: list[str] = []

    def publish(self, post, /):
        self.published.append(post.candidate.stem)
        return PublishResult(success=True, simulated=False, platform_post_id=f"urn:li:share:{len(self.published)}")


# schedule: accepted


def test_schedule_stores_a_simulated_schedule_with_both_hashes_in_utc(settings, capsys):
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    [(stem, mode, status, publish_at, media, caption)] = rows(settings)
    assert (stem, mode, status) == ("launch", "simulated", "SCHEDULED")
    assert publish_at == "2026-12-01T04:00:00Z"  # 09:00 in Karachi.
    assert media == sha256_file(incoming(settings) / "launch.jpg")
    assert caption == sha256_file(incoming(settings) / "launch.txt")
    out = capsys.readouterr().out
    assert "Scheduled 'launch' for 2026-12-01 09:00 Asia/Karachi (2026-12-01 04:00 UTC)" in out
    assert "simulated: nothing will be sent to LinkedIn" in out
    assert "run --scheduled" in out  # Says how the post will be published.


def test_schedule_real_needs_the_flag(settings, capsys):
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0

    assert rows(settings)[0][1] == "real"
    assert "REAL" in capsys.readouterr().out


def test_schedule_finds_the_post_without_case(settings):
    add_post(settings, "Launch")

    assert schedule("LAUNCH", "--at", "2026-12-01 09:00") == 0

    assert rows(settings)[0][0] == "Launch"


def test_schedule_is_logged_without_the_caption(settings):
    add_post(settings, "launch", caption=b"Secret caption text")

    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    log = settings.log_file.read_text(encoding="utf-8")
    assert "Schedule created:" in log and "stem=launch" in log and "publish_at=2026-12-01T04:00:00Z" in log
    assert "Secret caption text" not in log


def test_env_switches_cannot_make_a_schedule_real(settings, base, monkeypatch):
    (base / ".env").write_text("AUTOPOSTER_MODE=real\nAUTOPOSTER_REAL=1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_MODE", "real")
    monkeypatch.setenv("AUTOPOSTER_REAL", "1")
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    assert rows(settings)[0][1] == "simulated"


# schedule: refused (exit 1, nothing written, files untouched)


def assert_refused(settings, capsys, *args: str, message: str) -> None:
    before = sorted(p.name for p in incoming(settings).iterdir())
    assert schedule(*args) == 1
    err = capsys.readouterr().err
    assert message in err, err
    assert sorted(p.name for p in incoming(settings).iterdir()) == before


def test_refused_when_the_post_does_not_exist(settings, capsys):
    assert_refused(settings, capsys, "nothing", "--at", "2026-12-01 09:00", message="no post named 'nothing'")
    assert rows(settings) == []


def test_refused_when_the_caption_is_missing(settings, capsys):
    (incoming(settings) / "lonely.jpg").write_bytes(JPEG)
    assert_refused(settings, capsys, "lonely", "--at", "2026-12-01 09:00", message="waiting for its partner")
    assert rows(settings) == []


def test_refused_when_the_name_is_ambiguous(settings, capsys):
    add_post(settings, "banner")
    (incoming(settings) / "banner.png").write_bytes(PNG)
    assert_refused(settings, capsys, "banner", "--at", "2026-12-01 09:00", message="ambiguous")
    assert rows(settings) == []


def test_refused_while_a_file_is_still_being_copied(settings, capsys, monkeypatch):
    add_post(settings, "launch")
    monkeypatch.setattr(cli, "is_file_stable", lambda path, interval_s: path.suffix != ".jpg")
    assert_refused(settings, capsys, "launch", "--at", "2026-12-01 09:00", message="still being copied")
    assert rows(settings) == []


def test_refused_when_the_caption_is_too_long(settings, capsys):
    add_post(settings, "long", caption=b"x" * 3001)
    assert_refused(settings, capsys, "long", "--at", "2026-12-01 09:00",
                   message="caption is 3001 characters; LinkedIn allows at most 3000")
    assert rows(settings) == []


def test_a_real_schedule_checks_the_image_pixel_limit(settings, capsys):
    add_post(settings, "huge", image=huge_png(), ext=".png")
    assert_refused(settings, capsys, "huge", "--at", "2026-12-01 09:00", "--real", message="pixels")
    assert rows(settings) == []


def test_a_simulated_schedule_does_not_check_the_pixel_limit(settings):
    add_post(settings, "huge", image=huge_png(), ext=".png")

    assert schedule("huge", "--at", "2026-12-01 09:00") == 0


@pytest.mark.parametrize(("at", "message"), [
    ("2026-11-17 12:04", "at least 5 minutes ahead"),
    ("2027-11-17 12:01", "at most 1 year ahead"),
])
def test_refused_when_the_time_is_too_soon_or_too_far(settings, capsys, at, message):
    add_post(settings, "launch")
    assert_refused(settings, capsys, "launch", "--at", at, message=message)
    assert rows(settings) == []


@pytest.mark.parametrize(("at", "message"), [("2027-03-28 01:30", "does not exist"),
                                             ("2027-10-31 01:30", "happens twice")])
def test_refused_at_a_clock_change(settings, capsys, monkeypatch, at, message):
    monkeypatch.setenv("AUTOPOSTER_TIMEZONE", "Europe/London")
    add_post(settings, "launch")
    assert_refused(settings, capsys, "launch", "--at", at, message=message)
    assert rows(settings) == []


def test_refused_when_the_content_already_has_a_live_job_in_that_mode(settings, capsys):
    add_post(settings, "launch")
    assert cli.main(["run", "--interval", "0"]) == 0  # A SIMULATED job now holds this content.
    capsys.readouterr()

    assert_refused(settings, capsys, "launch", "--at", "2026-12-01 09:00", message="already handled by job")
    assert rows(settings) == []


def test_a_simulated_job_does_not_block_a_real_schedule(settings):
    add_post(settings, "launch")
    assert cli.main(["run", "--interval", "0"]) == 0

    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0


def test_refused_when_the_same_content_is_scheduled_under_another_name(settings, capsys):
    add_post(settings, "first", caption=b"Same words")
    add_post(settings, "second", caption=b"Same words")
    assert schedule("first", "--at", "2026-12-01 09:00") == 0
    capsys.readouterr()

    assert schedule("second", "--at", "2026-12-02 09:00") == 1

    assert "same content is already scheduled as 'first'" in capsys.readouterr().err
    assert len(rows(settings)) == 1


def test_refused_when_the_post_is_already_scheduled(settings, capsys):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0
    capsys.readouterr()

    assert schedule("launch", "--at", "2026-12-02 09:00") == 1

    assert "already scheduled" in capsys.readouterr().err
    assert len(rows(settings)) == 1


# schedule: other exits


@pytest.mark.parametrize("at", ["tomorrow", "2026-12-01T09:00", "2026-12-01 9:00", "2026-02-30 09:00"])
def test_a_badly_formatted_time_is_a_usage_error(settings, capsys, at):
    add_post(settings, "launch")

    with pytest.raises(SystemExit) as raised:
        schedule("launch", "--at", at)

    assert raised.value.code == 2
    assert "YYYY-MM-DD HH:MM" in capsys.readouterr().err
    assert rows(settings) == []


def test_a_missing_time_is_a_usage_error(settings):
    add_post(settings, "launch")

    with pytest.raises(SystemExit) as raised:
        cli.main(["schedule", "launch"])

    assert raised.value.code == 2


def test_schedule_is_refused_while_the_run_lock_is_held(settings, capsys):
    add_post(settings, "launch")

    with single_run_lock(settings.lock_file):
        assert schedule("launch", "--at", "2026-12-01 09:00") == 4

    assert rows(settings) == []


# schedule: the token warning (real only; never a LinkedIn call)


def write_token(base: Path, created: str | None) -> None:
    lines = ["LINKEDIN_ACCESS_TOKEN=AQVtesttoken1234567890abcdef"]
    if created is not None:
        lines.append(f"LINKEDIN_TOKEN_CREATED={created}")
    (base / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_real_schedule_warns_when_the_token_is_missing(settings, capsys):
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0

    assert "Warning: LINKEDIN_ACCESS_TOKEN is not set" in capsys.readouterr().err


def test_real_schedule_warns_when_the_token_expires_before_the_post(settings, base, capsys):
    write_token(base, "2026-11-01")  # Expires 2026-12-31.
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2027-01-05 09:00", "--real") == 0

    err = capsys.readouterr().err
    assert "Warning: the LinkedIn access token expires on 2026-12-31" in err
    assert "AQVtesttoken" not in err


def test_real_schedule_does_not_warn_when_the_token_outlasts_the_post(settings, base, capsys):
    write_token(base, "2026-11-01")
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0

    assert "Warning" not in capsys.readouterr().err


def test_real_schedule_warns_when_the_token_date_is_unknown(settings, base, capsys):
    write_token(base, None)
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0

    assert "Warning: LINKEDIN_TOKEN_CREATED is not set" in capsys.readouterr().err


def test_simulated_schedule_never_warns_about_the_token(settings, capsys):
    add_post(settings, "launch")

    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    assert "Warning" not in capsys.readouterr().err


# unschedule


def test_unschedule_cancels_and_leaves_the_files(settings, capsys):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    assert cli.main(["unschedule", "LAUNCH"]) == 0

    assert rows(settings)[0][2] == "CANCELLED"
    assert (incoming(settings) / "launch.jpg").exists() and (incoming(settings) / "launch.txt").exists()
    assert "Cancelled the schedule of 'launch'" in capsys.readouterr().out


def test_unschedule_without_an_active_schedule_is_refused(settings, capsys):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0
    assert cli.main(["unschedule", "launch"]) == 0
    capsys.readouterr()

    assert cli.main(["unschedule", "launch"]) == 1

    assert "'launch' has no active schedule" in capsys.readouterr().err


def test_unschedule_is_refused_while_the_run_lock_is_held(settings):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    with single_run_lock(settings.lock_file):
        assert cli.main(["unschedule", "launch"]) == 4

    assert rows(settings)[0][2] == "SCHEDULED"


# schedules (read-only)


def test_schedules_lists_active_schedules_in_local_time_and_utc(settings, capsys):
    add_post(settings, "launch")
    add_post(settings, "later")
    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0
    assert schedule("later", "--at", "2026-12-02 10:30") == 0
    capsys.readouterr()

    assert cli.main(["schedules"]) == 0

    out = capsys.readouterr().out
    launch = next(line for line in out.splitlines() if "launch" in line)
    assert "real" in launch and "2026-12-01 09:00" in launch and "2026-12-01 04:00" in launch and "SCHEDULED" in launch
    assert out.index("launch") < out.index("later")  # Soonest first.


def test_schedules_shows_only_active_unless_all_or_status(settings, capsys):
    add_post(settings, "launch")
    add_post(settings, "other")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0
    assert schedule("other", "--at", "2026-12-01 10:00") == 0
    assert cli.main(["unschedule", "other"]) == 0
    capsys.readouterr()

    assert cli.main(["schedules"]) == 0
    active = capsys.readouterr().out
    assert cli.main(["schedules", "--all"]) == 0
    every = capsys.readouterr().out
    assert cli.main(["schedules", "--status", "cancelled"]) == 0
    cancelled = capsys.readouterr().out

    assert "launch" in active and "other" not in active
    assert "launch" in every and "other" in every and "CANCELLED" in every
    assert "other" in cancelled and "launch" not in cancelled


def test_schedules_without_a_database_creates_nothing(settings, capsys):
    assert cli.main(["schedules"]) == 0

    assert "No schedules yet" in capsys.readouterr().out
    assert not settings.app_db_path.exists()


def test_schedules_on_a_version_1_database_does_not_migrate_it(settings, capsys):
    make_v1_database(settings.app_db_path)
    before = settings.app_db_path.read_bytes()

    assert cli.main(["schedules"]) == 0

    assert "No schedules yet" in capsys.readouterr().out
    assert user_version(settings.app_db_path) == 1
    assert settings.app_db_path.read_bytes() == before
    assert [p.name for p in settings.app_db_path.parent.iterdir()] == ["app.db"]


def test_scan_on_a_version_1_database_works_and_does_not_migrate_it(settings, capsys):
    make_v1_database(settings.app_db_path)
    before = settings.app_db_path.read_bytes()
    add_post(settings, "launch")

    assert cli.main(["scan", "--interval", "0"]) == 0

    out = capsys.readouterr().out
    assert "launch" in out and "valid   new" in out
    assert "SCHEDULED" not in out
    assert user_version(settings.app_db_path) == 1
    assert settings.app_db_path.read_bytes() == before
    assert [p.name for p in settings.app_db_path.parent.iterdir()] == ["app.db"]


# A bad AUTOPOSTER_TIMEZONE: a plain error, exit 1, no traceback


@pytest.mark.parametrize("command", [["scan", "--interval", "0"], ["run", "--interval", "0"], ["schedules"],
                                     ["schedule", "launch", "--at", "2026-12-01 09:00"], ["unschedule", "launch"],
                                     ["jobs"]])
def test_a_bad_timezone_is_a_plain_error(settings, capsys, monkeypatch, command):
    monkeypatch.setenv("AUTOPOSTER_TIMEZONE", "Mars/Olympus_Mons")
    add_post(settings, "launch")

    assert cli.main(command) == 1

    err = capsys.readouterr().err
    assert err.startswith("Error: AUTOPOSTER_TIMEZONE='Mars/Olympus_Mons' is not a known time zone")
    assert "Traceback" not in err


# Every other run skips posts with an active schedule


def test_a_run_skips_a_scheduled_post_and_processes_the_rest(settings, capsys):
    add_post(settings, "launch")
    add_post(settings, "other")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0
    capsys.readouterr()

    assert cli.main(["run", "--interval", "0"]) == 0

    assert job_rows(settings) == [("other", "simulated", "SIMULATED")]
    assert (incoming(settings) / "launch.jpg").exists()
    out = capsys.readouterr().out
    assert re.search(r"SCHEDULED \(1\).*\n\s+launch\s+2026-12-01 09:00 Asia/Karachi \(simulated\)", out)
    log = settings.log_file.read_text(encoding="utf-8")
    assert "Job skipped:" in log and "result=SCHEDULED" in log
    assert "scheduled=1" in log.strip().splitlines()[-1]


def test_run_real_never_publishes_a_scheduled_post(settings, monkeypatch, capsys):
    fake = FakePublisher()
    monkeypatch.setattr(cli, "_real_publisher", lambda s: fake)
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0

    assert cli.main(["run", "--real", "--interval", "0"]) == 0

    assert fake.published == []
    assert job_rows(settings) == []
    assert (incoming(settings) / "launch.jpg").exists()


def test_a_scheduled_post_that_was_simulated_does_not_trigger_the_only_simulated_refusal(settings, monkeypatch, capsys):
    fake = FakePublisher()
    monkeypatch.setattr(cli, "_real_publisher", lambda s: fake)
    add_post(settings, "launch")
    assert cli.main(["run", "--interval", "0"]) == 0  # Simulated first.
    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0
    capsys.readouterr()

    assert cli.main(["run", "--real", "--interval", "0"]) == 0

    assert "REFUSED" not in capsys.readouterr().out
    assert fake.published == []


@pytest.mark.parametrize(("schedule_mode", "run_args"), [([], ["--real"]), (["--real"], [])])
def test_runs_skip_scheduled_posts_of_either_mode(settings, monkeypatch, schedule_mode, run_args):
    fake = FakePublisher()
    monkeypatch.setattr(cli, "_real_publisher", lambda s: fake)
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00", *schedule_mode) == 0

    assert cli.main(["run", "--interval", "0", *run_args]) == 0

    assert job_rows(settings) == [] and fake.published == []


def test_a_cancelled_schedule_no_longer_holds_the_post(settings):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0
    assert cli.main(["unschedule", "launch"]) == 0

    assert cli.main(["run", "--interval", "0"]) == 0

    assert job_rows(settings) == [("launch", "simulated", "SIMULATED")]


def test_scan_marks_a_scheduled_post(settings, capsys):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0
    capsys.readouterr()

    assert cli.main(["scan", "--interval", "0"]) == 0

    line = next(line for line in capsys.readouterr().out.splitlines() if "launch.jpg" in line)
    assert "SCHEDULED for 2026-12-01 09:00 Asia/Karachi (real)" in line


def test_schedules_reads_with_a_read_only_connection(settings, monkeypatch):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0

    def no_writes(path):
        raise AssertionError("schedules must not open the database for writing")

    monkeypatch.setattr(cli, "open_app_db", no_writes)
    assert cli.main(["schedules"]) == 0


def test_the_schedule_repository_sees_the_cli_schedule(settings):
    add_post(settings, "launch")
    assert schedule("launch", "--at", "2026-12-01 09:00", "--real") == 0

    conn = open_app_db(settings.app_db_path)
    try:
        record = ScheduleRepository(conn).find_active(platform=LINKEDIN, stem="launch")
    finally:
        conn.close()
    assert record.mode is PublishMode.REAL and record.status is ScheduleStatus.SCHEDULED
