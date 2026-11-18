"""run --scheduled (D46, commit 6): publishing due schedules, and only them.

Every test has its own base and data folder and a controllable clock (``main._now``); none reads the real time.
LinkedIn is never contacted: the real publisher is replaced by a fake that records posts, and in tests that use
the real token check, the LinkedIn client itself fails the test if it is ever created.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.operations import clock as clock_module
from social_media_auto_poster.config import Settings, ensure_data_dirs
from social_media_auto_poster.models import Platform, PublishResult

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
LINKEDIN = Platform.LINKEDIN
SCHEDULED_AT = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)  # When the schedules are made (12:00 in Karachi).
DUE = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)  # "2026-12-01 09:00" in Karachi.


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
    monkeypatch.setattr(cli, "_linkedin_client", _no_linkedin)
    return tmp_path


@pytest.fixture
def settings(base) -> Settings:
    settings = Settings(base_dir=base, data_dir=base / "data")
    ensure_data_dirs(settings)
    return settings


@pytest.fixture
def linkedin(monkeypatch) -> FakePublisher:
    fake = FakePublisher()
    monkeypatch.setattr(cli, "_real_publisher", lambda settings: fake)
    return fake


def _no_linkedin(token):
    raise AssertionError("LinkedIn must not be contacted in this test")


def incoming(settings: Settings) -> Path:
    return settings.incoming_dir(LINKEDIN)


def add_post(settings: Settings, stem: str, caption: bytes | None = None) -> None:
    (incoming(settings) / f"{stem}.jpg").write_bytes(JPEG)
    (incoming(settings) / f"{stem}.txt").write_bytes(caption if caption is not None else f"Hello from {stem}".encode())


def schedule(stem: str, at: str = "2026-12-01 09:00", *, real: bool = False) -> None:
    assert cli.main(["schedule", stem, "--at", at, "--interval", "0", *(["--real"] if real else [])]) == 0


def run(*args: str) -> int:
    return cli.main(["run", "--interval", "0", *args])


def query(settings: Settings, sql: str) -> list[tuple]:
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def jobs(settings: Settings) -> list[tuple]:
    return query(settings, "SELECT stem, mode, status FROM jobs ORDER BY created_at, rowid")


def schedule_states(settings: Settings) -> dict[str, tuple]:
    return {stem: (status, job_id, reason) for stem, status, job_id, reason in
            query(settings, "SELECT stem, status, job_id, reason FROM schedules")}


# Publishing what is due, and nothing else


def test_scheduled_run_publishes_a_due_simulated_schedule_and_nothing_else(settings, clock, capsys):
    add_post(settings, "launch")
    add_post(settings, "other")  # Unscheduled: a scheduled run never touches it.
    schedule("launch")
    clock["now"] = DUE + timedelta(minutes=10)

    assert run("--scheduled") == 0

    assert jobs(settings) == [("launch", "simulated", "SIMULATED")]
    status, job_id, _ = schedule_states(settings)["launch"]
    assert status == "DONE" and job_id is not None
    assert (incoming(settings) / "other.jpg").exists()
    assert "Simulation mode: nothing was sent to LinkedIn in this run." in capsys.readouterr().out


def test_real_scheduled_run_publishes_archives_and_settles_the_schedule(settings, clock, linkedin):
    add_post(settings, "launch")
    add_post(settings, "other")
    schedule("launch", real=True)
    clock["now"] = DUE

    assert run("--real", "--scheduled") == 0

    assert linkedin.published == ["launch"]
    assert jobs(settings) == [("launch", "real", "PUBLISHED")]
    assert schedule_states(settings)["launch"][0] == "DONE"
    assert not (incoming(settings) / "launch.jpg").exists()  # Archived after a confirmed publish.
    assert (incoming(settings) / "other.jpg").exists()


def test_a_future_schedule_is_left_alone(settings, clock, capsys):
    add_post(settings, "launch")
    schedule("launch")
    clock["now"] = DUE - timedelta(minutes=1)
    capsys.readouterr()

    assert run("--scheduled") == 0

    assert jobs(settings) == []
    assert schedule_states(settings)["launch"][0] == "SCHEDULED"
    assert "No scheduled post is due." in capsys.readouterr().out


def test_at_most_one_per_run_oldest_first(settings, clock):
    add_post(settings, "later")
    add_post(settings, "earlier")
    schedule("later", "2026-12-01 09:00")
    schedule("earlier", "2026-12-01 08:30")
    clock["now"] = DUE + timedelta(minutes=5)

    assert run("--scheduled") == 0
    assert [stem for stem, _, _ in jobs(settings)] == ["earlier"]
    assert schedule_states(settings)["later"][0] == "SCHEDULED"

    assert run("--scheduled") == 0
    assert [stem for stem, _, _ in jobs(settings)] == ["earlier", "later"]


def test_a_late_post_within_6_hours_is_published_and_the_log_says_how_late(settings, clock, capsys):
    add_post(settings, "launch")
    schedule("launch")
    clock["now"] = DUE + timedelta(minutes=90)
    capsys.readouterr()

    assert run("--scheduled") == 0

    assert jobs(settings) == [("launch", "simulated", "SIMULATED")]
    log = settings.log_file.read_text(encoding="utf-8")
    assert "Scheduled post due:" in log and "late_by_minutes=90" in log
    assert "90 min late" in capsys.readouterr().out


def test_more_than_6_hours_late_is_missed_and_nothing_is_sent(settings, clock, linkedin, capsys):
    add_post(settings, "launch")
    schedule("launch", real=True)
    clock["now"] = DUE + timedelta(hours=6, minutes=1)
    capsys.readouterr()

    assert run("--real", "--scheduled") == 3

    assert linkedin.published == [] and jobs(settings) == []
    status, job_id, reason = schedule_states(settings)["launch"]
    assert status == "MISSED" and job_id is None and "more than 6 hours late" in reason
    assert "MISSED (1)" in capsys.readouterr().out
    assert (incoming(settings) / "launch.jpg").exists()


def test_missed_schedules_do_not_use_up_the_one_post_per_run(settings, clock):
    add_post(settings, "missed")
    add_post(settings, "in-time")
    schedule("missed", "2026-12-01 09:00")  # 04:00 UTC
    schedule("in-time", "2026-12-01 16:00")  # 11:00 UTC
    clock["now"] = datetime(2026, 12, 1, 11, 5, tzinfo=UTC)

    run("--scheduled")

    states = schedule_states(settings)
    assert states["missed"][0] == "MISSED"
    assert states["in-time"][0] == "DONE"
    assert jobs(settings) == [("in-time", "simulated", "SIMULATED")]


# The token and the other real-run safeguards


def test_nothing_due_means_no_token_check_and_no_linkedin_call(settings, clock, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_real_publisher", lambda s: pytest.fail("no real publisher when nothing is due"))
    add_post(settings, "launch")
    schedule("launch", real=True)
    clock["now"] = DUE - timedelta(hours=1)

    assert run("--real", "--scheduled") == 0

    assert schedule_states(settings)["launch"][0] == "SCHEDULED"


def test_an_expired_token_with_something_due_stops_the_run_and_changes_nothing(settings, base, clock, capsys):
    (base / ".env").write_text("LINKEDIN_ACCESS_TOKEN=AQVtesttoken\nLINKEDIN_TOKEN_CREATED=2020-01-01\n",
                               encoding="utf-8")
    add_post(settings, "missed")
    add_post(settings, "due")
    schedule("missed", "2026-12-01 09:00", real=True)
    schedule("due", "2026-12-01 16:00", real=True)
    clock["now"] = datetime(2026, 12, 1, 11, 5, tzinfo=UTC)

    assert run("--real", "--scheduled") == 1  # A token created on 2020-01-01 is expired on any date.

    assert "expired" in capsys.readouterr().err
    assert {stem: state[0] for stem, state in schedule_states(settings).items()} == {
        "missed": "SCHEDULED", "due": "SCHEDULED",  # Not even the missed one changed: refused before anything.
    }
    assert jobs(settings) == []


def test_a_due_real_schedule_is_exempt_from_the_only_simulated_refusal(settings, clock, linkedin, capsys):
    add_post(settings, "launch")
    add_post(settings, "other")
    assert run() == 0  # Both simulated: a plain run --real would refuse them.
    schedule("launch", real=True)
    clock["now"] = DUE

    assert run("--real", "--scheduled") == 0

    assert "REFUSED" not in capsys.readouterr().out
    assert linkedin.published == ["launch"]
    assert ("other", "real", "PUBLISHED") not in jobs(settings)


def test_the_archive_history_check_still_applies(settings, clock, linkedin, capsys):
    add_post(settings, "launch")
    schedule("launch", real=True)
    folder = settings.archive_dir(LINKEDIN) / "20261001T000000Z_old"
    folder.mkdir(parents=True)
    (folder / "old.jpg").write_bytes(JPEG)
    clock["now"] = DUE

    assert run("--real", "--scheduled") == 1

    assert "REFUSED: archive/ holds" in capsys.readouterr().err
    assert linkedin.published == [] and schedule_states(settings)["launch"][0] == "SCHEDULED"


def test_the_data_folder_check_still_applies(settings, clock, linkedin, capsys):
    add_post(settings, "launch")
    schedule("launch", real=True)
    settings.failed_dir(LINKEDIN).rmdir()
    clock["now"] = DUE

    assert run("--real", "--scheduled") == 1

    assert "does not have its LinkedIn folders" in capsys.readouterr().err
    assert linkedin.published == [] and schedule_states(settings)["launch"][0] == "SCHEDULED"


# Modes, .env, and usage


def test_a_simulated_scheduled_run_ignores_real_schedules(settings, clock, monkeypatch):
    monkeypatch.setattr(cli, "_real_publisher", lambda s: pytest.fail("a simulated run never makes a real publisher"))
    add_post(settings, "real-one")
    add_post(settings, "sim-one")
    schedule("real-one", real=True)
    schedule("sim-one")
    clock["now"] = DUE

    assert run("--scheduled") == 0

    states = schedule_states(settings)
    assert states["real-one"][0] == "SCHEDULED" and states["sim-one"][0] == "DONE"


def test_a_real_scheduled_run_ignores_simulated_schedules(settings, clock, linkedin):
    add_post(settings, "real-one")
    add_post(settings, "sim-one")
    schedule("real-one", real=True)
    schedule("sim-one")
    clock["now"] = DUE

    assert run("--real", "--scheduled") == 0

    states = schedule_states(settings)
    assert states["real-one"][0] == "DONE" and states["sim-one"][0] == "SCHEDULED"
    assert linkedin.published == ["real-one"]


def test_env_can_never_make_a_scheduled_run_real(settings, base, clock, monkeypatch):
    (base / ".env").write_text("AUTOPOSTER_MODE=real\nAUTOPOSTER_REAL=1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_MODE", "real")
    monkeypatch.setenv("AUTOPOSTER_REAL", "1")
    monkeypatch.setattr(cli, "_real_publisher", lambda s: pytest.fail("only --real makes a real publisher"))
    add_post(settings, "launch")
    schedule("launch", real=True)
    clock["now"] = DUE

    assert run("--scheduled") == 0

    assert schedule_states(settings)["launch"][0] == "SCHEDULED" and jobs(settings) == []


@pytest.mark.parametrize("extra", [[], ["--real"]])
def test_scheduled_with_publish_simulated_is_a_usage_error(settings, capsys, extra):
    assert run("--scheduled", "--publish-simulated", *extra) == 2

    assert "--scheduled" in capsys.readouterr().err


# Content checks at the scheduled time (the 5a workflow paths, reached through the run)


def test_changed_content_at_the_scheduled_time_is_invalid_and_not_published(settings, clock, linkedin, capsys):
    add_post(settings, "launch")
    schedule("launch", real=True)
    (incoming(settings) / "launch.txt").write_bytes(b"Edited after scheduling")
    clock["now"] = DUE
    capsys.readouterr()

    assert run("--real", "--scheduled") == 3

    assert linkedin.published == [] and jobs(settings) == []
    status, _, reason = schedule_states(settings)["launch"]
    assert status == "INVALID" and reason == "content changed after scheduling"
    out = capsys.readouterr().out
    assert "SCHEDULE INVALID" in out and "content changed after scheduling" in out
    assert (incoming(settings) / "launch.jpg").exists()


def test_a_deleted_file_at_the_scheduled_time_is_invalid(settings, clock, linkedin):
    add_post(settings, "launch")
    schedule("launch", real=True)
    (incoming(settings) / "launch.jpg").unlink()
    clock["now"] = DUE

    assert run("--real", "--scheduled") == 3

    status, _, reason = schedule_states(settings)["launch"]
    assert status == "INVALID" and reason == "file missing: launch.jpg"
    assert linkedin.published == []


def test_a_scheduled_run_logs_start_and_finish(settings, clock):
    add_post(settings, "launch")
    schedule("launch")
    clock["now"] = DUE

    run("--scheduled")

    lines = settings.log_file.read_text(encoding="utf-8").strip().splitlines()
    assert any("Run started:" in line and "scheduled=yes" in line for line in lines)
    assert "Run finished:" in lines[-1]
