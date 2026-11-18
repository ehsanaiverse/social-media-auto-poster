"""Early warning about changed schedules, and the reconfirm command (D46, commit 5b).

Every test has its own base and data folder, and a fixed clock (``main._now``); none reads the real time.
No test reaches LinkedIn: the network is blocked (conftest) and no LinkedIn client is ever created.
"""

import sqlite3
import struct
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations import scan as scan_ops
from social_media_auto_poster.operations import clock
from social_media_auto_poster.config import Settings, ensure_data_dirs
from social_media_auto_poster.models import JobStatus, Platform, PublishMode, ScheduleStatus
from social_media_auto_poster.services import scheduling
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository
from social_media_auto_poster.storage.schedules import ScheduleRepository

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG = (IMAGES / "baseline-7x5.jpg").read_bytes()
PNG = (IMAGES / "image-6x3.png").read_bytes()
NOW = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)  # 12:00 in Karachi.
LINKEDIN = Platform.LINKEDIN
CHANGED_LABEL = "CHANGED – will not be published; re-confirm"
MISSING_LABEL = "FILE MISSING – will not be published; re-confirm"


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    monkeypatch.delenv("AUTOPOSTER_TIMEZONE", raising=False)
    monkeypatch.setattr(clock, "now", lambda: NOW)
    monkeypatch.setattr(linkedin_ops, "_linkedin_client", _no_linkedin)
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


def add_post(settings: Settings, stem: str = "launch", *, image: bytes = JPEG, ext: str = ".jpg",
             caption: bytes | None = None) -> None:
    (incoming(settings) / f"{stem}{ext}").write_bytes(image)
    (incoming(settings) / f"{stem}.txt").write_bytes(caption if caption is not None else f"Hello from {stem}".encode())


def schedule(*args: str) -> int:
    return cli.main(["schedule", *args, "--interval", "0"])


def reconfirm(*args: str) -> int:
    return cli.main(["reconfirm", *args, "--interval", "0"])


def rows(settings: Settings) -> list[tuple]:
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute(
            "SELECT stem, mode, status, publish_at, caption_sha256, reason FROM schedules ORDER BY created_at, rowid"
        ).fetchall()
    finally:
        conn.close()


def repo_call(settings: Settings, action):
    conn = open_app_db(settings.app_db_path)
    try:
        return action(ScheduleRepository(conn), JobRepository(conn))
    finally:
        conn.close()


def huge_png() -> bytes:
    return PNG[:16] + struct.pack(">II", 10000, 10000) + PNG[24:]


def scheduled(settings: Settings, stem="launch", at="2026-12-01 09:00", real=False) -> None:
    add_post(settings, stem)
    assert schedule(stem, "--at", at, *(["--real"] if real else [])) == 0


def edit_caption(settings: Settings, stem="launch", text=b"Edited after scheduling") -> None:
    (incoming(settings) / f"{stem}.txt").write_bytes(text)


# 19-22. Early warning: read-only


def test_schedules_shows_changed_and_never_writes(settings, capsys, monkeypatch):
    scheduled(settings)
    edit_caption(settings)
    before = settings.app_db_path.read_bytes()
    capsys.readouterr()
    monkeypatch.setattr(cli, "open_app_db", lambda path: pytest.fail("schedules must not open the database for writing"))

    assert cli.main(["schedules"]) == 0

    assert CHANGED_LABEL in capsys.readouterr().out
    assert settings.app_db_path.read_bytes() == before
    assert rows(settings)[0][2] == "SCHEDULED"


def test_schedules_shows_file_missing(settings, capsys):
    scheduled(settings)
    (incoming(settings) / "launch.jpg").unlink()
    capsys.readouterr()

    assert cli.main(["schedules"]) == 0

    assert MISSING_LABEL in capsys.readouterr().out
    assert rows(settings)[0][2] == "SCHEDULED"


def test_schedules_shows_nothing_extra_for_unchanged_content(settings, capsys):
    scheduled(settings)
    capsys.readouterr()

    assert cli.main(["schedules"]) == 0

    out = capsys.readouterr().out
    assert "CHANGED" not in out and "FILE MISSING" not in out


def test_scan_shows_changed_and_never_writes(settings, capsys, monkeypatch):
    scheduled(settings)
    edit_caption(settings)
    before = settings.app_db_path.read_bytes()
    capsys.readouterr()
    monkeypatch.setattr(cli, "open_app_db", lambda path: pytest.fail("scan must not open the database for writing"))

    assert cli.main(["scan", "--interval", "0"]) == 0

    line = next(line for line in capsys.readouterr().out.splitlines() if "launch.jpg" in line)
    assert "SCHEDULED for 2026-12-01 09:00 Asia/Karachi (simulated)" in line and CHANGED_LABEL in line
    assert settings.app_db_path.read_bytes() == before


def test_scan_lists_a_schedule_whose_files_are_gone(settings, capsys):
    scheduled(settings)
    (incoming(settings) / "launch.txt").unlink()
    capsys.readouterr()

    assert cli.main(["scan", "--interval", "0"]) == 0

    out = capsys.readouterr().out
    assert "SCHEDULED, FILES MISSING (1)" in out
    line = next(line for line in out.splitlines() if line.strip().startswith("launch ") or "launch   " in line)
    assert MISSING_LABEL in line
    assert rows(settings)[0][2] == "SCHEDULED"


def test_list_schedules_reports_the_content_state(settings):
    scheduled(settings, "same")
    scheduled(settings, "edited", at="2026-12-02 09:00")
    scheduled(settings, "gone", at="2026-12-03 09:00")
    edit_caption(settings, "edited")
    (incoming(settings) / "gone.jpg").unlink()

    listing = cli.list_schedules(settings)

    assert {view.schedule.stem: view.content for view in listing.schedules} == {
        "same": "ok", "edited": "changed", "gone": "missing",
    }


def test_a_file_that_cannot_be_read_is_not_called_changed(settings, capsys, monkeypatch):
    scheduled(settings)
    capsys.readouterr()

    def locked(path, **kwargs):
        raise PermissionError(13, "locked by another program", str(path))

    monkeypatch.setattr(scheduling, "sha256_file", locked)

    assert cli.main(["schedules"]) == 0

    out = capsys.readouterr().out
    assert "could not read the files" in out and "CHANGED" not in out


# 23-27. reconfirm: time and mode


def test_reconfirm_keeps_the_time_and_binds_the_new_content(settings, capsys):
    scheduled(settings)
    edit_caption(settings)
    capsys.readouterr()

    assert reconfirm("launch") == 0

    (old_stem, _, old_status, old_at, old_caption, old_reason), new = rows(settings)
    assert old_status == "CANCELLED" and old_reason.startswith("re-confirmed as ")
    assert new[2] == "SCHEDULED" and new[3] == old_at == "2026-12-01T04:00:00Z"
    assert new[4] == sha256_file(incoming(settings) / "launch.txt") != old_caption
    assert "Re-confirmed 'launch' for 2026-12-01 09:00 Asia/Karachi" in capsys.readouterr().out


def test_reconfirm_with_at_sets_a_new_time(settings):
    scheduled(settings)

    assert reconfirm("launch", "--at", "2026-12-05 18:30") == 0

    assert rows(settings)[1][3] == "2026-12-05T13:30:00Z"


def test_reconfirm_refuses_an_old_time_that_is_too_close(settings, capsys):
    add_post(settings)
    soon = NOW + timedelta(minutes=3)
    repo_call(settings, lambda schedules, jobs: schedules.create(
        schedule_id=str(uuid.uuid4()), platform=LINKEDIN, mode=PublishMode.SIMULATED, stem="launch",
        media_name="launch.jpg", caption_name="launch.txt", media_sha256=sha256_file(incoming(settings) / "launch.jpg"),
        caption_sha256=sha256_file(incoming(settings) / "launch.txt"), publish_at=soon))
    before = rows(settings)

    assert reconfirm("launch") == 1

    assert "give --at" in capsys.readouterr().err
    assert rows(settings) == before


def test_reconfirm_without_real_makes_a_real_schedule_simulated_and_says_so(settings, capsys):
    scheduled(settings, real=True)
    capsys.readouterr()

    assert reconfirm("launch") == 0

    assert [r[1] for r in rows(settings)] == ["real", "simulated"]
    err = capsys.readouterr().err
    assert "the old schedule was REAL; this one is SIMULATED because --real was not given" in err


def test_reconfirm_with_real_is_real_and_warns_about_the_token(settings, capsys):
    scheduled(settings)
    capsys.readouterr()

    assert reconfirm("launch", "--real") == 0

    assert rows(settings)[1][1] == "real"
    assert "Warning: LINKEDIN_ACCESS_TOKEN is not set" in capsys.readouterr().err


# 28. reconfirm refuses what schedule refuses (exit 1, nothing changed)


def refusal_setups():
    def missing_post(s):
        (incoming(s) / "launch.jpg").unlink()
        (incoming(s) / "launch.txt").unlink()

    def pending(s):
        (incoming(s) / "launch.txt").unlink()

    def ambiguous(s):
        (incoming(s) / "launch.png").write_bytes(PNG)

    def too_long(s):
        edit_caption(s, text=b"x" * 3001)

    def duplicate_job(s):
        edit_caption(s, text=b"Already posted")
        caption = sha256_file(incoming(s) / "launch.txt")
        media = sha256_file(incoming(s) / "launch.jpg")
        repo_call(s, lambda schedules, jobs: jobs.create(
            job_id="earlier-job", platform=LINKEDIN, mode=PublishMode.SIMULATED, stem="old", media_name="old.jpg",
            caption_name="old.txt", media_sha256=media, caption_sha256=caption, status=JobStatus.SIMULATED))

    def same_content_elsewhere(s):
        add_post(s, "twin", caption=b"Twin words")
        assert schedule("twin", "--at", "2026-12-09 09:00") == 0
        edit_caption(s, text=b"Twin words")

    return [
        (missing_post, [], "no post named 'launch'"),
        (pending, [], "waiting for its partner"),
        (ambiguous, [], "ambiguous"),
        (too_long, [], "caption is 3001 characters"),
        (duplicate_job, [], "already handled by job"),
        (same_content_elsewhere, [], "same content is already scheduled as 'twin'"),
        (lambda s: None, ["--at", "2026-11-17 12:04"], "at least 5 minutes ahead"),
    ]


@pytest.mark.parametrize(("setup", "extra", "message"), refusal_setups(),
                         ids=["missing", "pending", "ambiguous", "too-long", "duplicate-job", "same-content",
                              "too-soon"])
def test_reconfirm_refuses_like_schedule(settings, capsys, setup, extra, message):
    scheduled(settings)
    setup(settings)
    before = rows(settings)
    capsys.readouterr()

    assert reconfirm("launch", *extra) == 1

    assert message in capsys.readouterr().err
    assert rows(settings) == before


def test_reconfirm_real_checks_the_pixel_limit(settings, capsys):
    add_post(settings, image=huge_png(), ext=".png")
    assert schedule("launch", "--at", "2026-12-01 09:00") == 0  # Simulated: the pixel limit does not apply.
    before = rows(settings)

    assert reconfirm("launch", "--real") == 1

    assert "pixels" in capsys.readouterr().err
    assert rows(settings) == before


def test_reconfirm_refuses_a_file_still_being_copied(settings, capsys, monkeypatch):
    scheduled(settings)
    monkeypatch.setattr(scan_ops, "is_file_stable", lambda path, interval_s: path.suffix != ".txt")
    before = rows(settings)

    assert reconfirm("launch") == 1

    assert "still being copied" in capsys.readouterr().err
    assert rows(settings) == before


# 29-30. Which schedule reconfirm works on


def test_reconfirm_works_from_the_latest_invalid_schedule(settings):
    scheduled(settings)
    old_id = repo_call(settings, lambda schedules, jobs: schedules.find_active(platform=LINKEDIN, stem="launch")).schedule_id
    repo_call(settings, lambda schedules, jobs: schedules.mark_invalid(old_id, reason="content changed after scheduling"))
    edit_caption(settings)

    assert reconfirm("launch") == 0

    old, new = rows(settings)
    assert old[2] == "INVALID" and old[5] == "content changed after scheduling"  # Stays as it was.
    assert new[2] == "SCHEDULED" and new[4] == sha256_file(incoming(settings) / "launch.txt")


@pytest.mark.parametrize("final", ["CANCELLED", "MISSED", "DONE"])
def test_reconfirm_refuses_when_the_latest_schedule_is_final(settings, capsys, final):
    scheduled(settings)
    old_id = repo_call(settings, lambda schedules, jobs: schedules.find_active(platform=LINKEDIN, stem="launch")).schedule_id

    def finish(schedules, jobs):
        if final == "CANCELLED":
            schedules.cancel(old_id)
        elif final == "MISSED":
            schedules.mark_missed(old_id, reason="more than 6 hours late")
        else:
            jobs.create(job_id="the-job", platform=LINKEDIN, mode=PublishMode.SIMULATED, stem="launch",
                        media_name="launch.jpg", caption_name="launch.txt", media_sha256=None, caption_sha256=None,
                        status=JobStatus.FAILED)
            schedules.mark_done(old_id, job_id="the-job")

    repo_call(settings, finish)
    before = rows(settings)

    assert reconfirm("launch") == 1

    assert "nothing to re-confirm" in capsys.readouterr().err
    assert rows(settings) == before


def test_reconfirm_without_any_schedule_is_refused(settings, capsys):
    add_post(settings)

    assert reconfirm("launch") == 1

    assert "nothing to re-confirm" in capsys.readouterr().err


# 31. Atomic


def test_reconfirm_is_atomic(settings, monkeypatch):
    scheduled(settings)
    edit_caption(settings)
    before = rows(settings)

    def broken_insert(self, *args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error (injected after the cancel)")

    monkeypatch.setattr(ScheduleRepository, "_insert", broken_insert)
    with pytest.raises(sqlite3.OperationalError):
        cli.reconfirm_post(settings, "launch", real=False, at=None, interval_s=0)

    assert rows(settings) == before  # Old still SCHEDULED, no new row: the cancel was rolled back.


# 32-34. Exits, unchanged content, .env


def test_reconfirm_is_refused_while_the_run_lock_is_held(settings):
    scheduled(settings)
    before = rows(settings)

    with single_run_lock(settings.lock_file):
        assert reconfirm("launch") == 4

    assert rows(settings) == before


def test_reconfirm_with_a_badly_formatted_time_is_a_usage_error(settings, capsys):
    scheduled(settings)

    with pytest.raises(SystemExit) as raised:
        reconfirm("launch", "--at", "tomorrow")

    assert raised.value.code == 2
    assert "YYYY-MM-DD HH:MM" in capsys.readouterr().err


def test_reconfirm_of_unchanged_content_is_allowed(settings):
    scheduled(settings)

    assert reconfirm("launch", "--real", "--at", "2026-12-02 10:00") == 0

    old, new = rows(settings)
    assert old[2] == "CANCELLED" and new[2] == "SCHEDULED" and new[1] == "real"
    assert new[4] == old[4]  # Same content: the old schedule did not count as "already scheduled elsewhere".


def test_env_switches_cannot_make_a_reconfirm_real(settings, base, monkeypatch):
    scheduled(settings)
    (base / ".env").write_text("AUTOPOSTER_MODE=real\nAUTOPOSTER_REAL=1\n", encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_MODE", "real")
    monkeypatch.setenv("AUTOPOSTER_REAL", "1")

    assert reconfirm("launch") == 0

    assert rows(settings)[1][1] == "simulated"


def test_reconfirm_is_logged_without_the_caption(settings):
    scheduled(settings)
    edit_caption(settings, text=b"Secret new caption")

    assert reconfirm("launch") == 0

    log = settings.log_file.read_text(encoding="utf-8")
    assert "Schedule re-confirmed:" in log and "stem=launch" in log
    assert "Secret new caption" not in log
