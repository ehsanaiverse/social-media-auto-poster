"""E1: ``--platform`` on the command line. Without it every command behaves exactly as before (LinkedIn).

Instagram is simulated only (D49): ``--real`` with it is refused before any lock, log or database. Facebook has
no integration and is refused. Listings (jobs, schedules) cover every platform unless filtered, and show a
PLATFORM column only when a listed row is not LinkedIn, so LinkedIn-only output is unchanged.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings, ensure_data_dirs
from social_media_auto_poster.models import Platform

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
D49 = "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set in backend\\.env"  # D48: real Instagram needs its credentials in backend\.env.


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    settings = Settings(base_dir=tmp_path, data_dir=tmp_path / "data")
    ensure_data_dirs(settings)
    return settings


def add_post(settings, platform: Platform, stem: str = "launch") -> None:
    folder = settings.incoming_dir(platform)
    (folder / f"{stem}.jpg").write_bytes(JPEG)
    (folder / f"{stem}.txt").write_text(f"Hello from {stem}", encoding="utf-8")


def main(*args: str) -> int:
    return cli.main(list(args))


def nothing_written(settings) -> bool:
    return not (settings.app_db_path.exists() or settings.lock_file.exists() or settings.log_file.exists())


# Instagram, simulated


def test_scan_and_run_for_instagram(settings, capsys):
    add_post(settings, Platform.INSTAGRAM)

    assert main("scan", "--platform", "instagram", "--interval", "0") == 0
    assert "Scanning Instagram incoming:" in capsys.readouterr().out

    assert main("run", "--platform", "Instagram", "--interval", "0") == 0
    out = capsys.readouterr().out
    assert out.startswith("Processing Instagram incoming:")
    assert "Simulation mode: nothing was sent to Instagram in this run." in out


def test_schedule_unschedule_and_schedules_for_instagram(settings, capsys):
    add_post(settings, Platform.INSTAGRAM)

    at = (datetime.now(settings.timezone) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    assert main("schedule", "launch", "--at", at, "--platform", "instagram", "--interval", "0") == 0
    assert "simulated: nothing will be sent to Instagram" in capsys.readouterr().out
    assert main("schedules") == 0
    listed = capsys.readouterr().out
    assert "PLATFORM" in listed and "Instagram" in listed
    assert main("unschedule", "launch", "--platform", "instagram") == 0
    assert "Cancelled the schedule of 'launch'" in capsys.readouterr().out


# Real Instagram and Facebook are refused


def test_a_real_instagram_run_without_credentials_publishes_nothing(settings, capsys):
    add_post(settings, Platform.INSTAGRAM)

    assert main("run", "--real", "--platform", "instagram", "--interval", "0") == 1

    captured = capsys.readouterr()
    assert D49 in captured.out + captured.err
    assert (settings.incoming_dir(Platform.INSTAGRAM) / "launch.jpg").is_file()


@pytest.mark.parametrize("command", [
    ("scan",), ("run",), ("jobs",), ("schedules",), ("schedule", "launch", "--at", "2099-12-01 09:00"),
    ("unschedule", "launch"), ("reconfirm", "launch"), ("resolve", "abcdef", "--failed"),
])
def test_facebook_is_refused(settings, capsys, command):
    assert main(*command, "--platform", "facebook") == 1

    assert "Facebook is not supported: it has folders but no integration" in capsys.readouterr().err
    assert nothing_written(settings)


def test_an_unknown_platform_is_a_usage_error(settings, capsys):
    with pytest.raises(SystemExit) as raised:
        main("scan", "--platform", "tiktok")
    assert raised.value.code == 2
    assert "unknown platform 'tiktok'" in capsys.readouterr().err


# Listings


def test_a_linkedin_only_job_list_is_unchanged_and_a_mixed_one_shows_the_platform(settings, capsys):
    add_post(settings, Platform.LINKEDIN, "li-post")
    assert main("run", "--interval", "0") == 0
    capsys.readouterr()

    assert main("jobs") == 0
    linkedin_only = capsys.readouterr().out
    assert "PLATFORM" not in linkedin_only
    assert linkedin_only.splitlines()[0].split() == ["JOB", "CREATED", "(UTC)", "STEM", "MODE", "STATUS"]

    add_post(settings, Platform.INSTAGRAM, "ig-post")
    assert main("run", "--platform", "instagram", "--interval", "0") == 0
    capsys.readouterr()

    assert main("jobs") == 0
    mixed = capsys.readouterr().out
    assert mixed.splitlines()[0].split()[:2] == ["JOB", "PLATFORM"]
    assert "LinkedIn" in mixed and "Instagram" in mixed

    assert main("jobs", "--platform", "instagram") == 0
    only = capsys.readouterr().out
    assert "ig-post" in only and "li-post" not in only

    assert main("jobs", "--platform", "linkedin") == 0
    assert capsys.readouterr().out == linkedin_only  # A LinkedIn-only list prints exactly as before.


def test_resolve_refuses_a_job_of_another_platform(settings, capsys):
    from social_media_auto_poster.models import JobStatus, PublishMode
    from social_media_auto_poster.storage.database import open_app_db
    from social_media_auto_poster.storage.jobs import JobRepository

    conn = open_app_db(settings.app_db_path)
    try:
        repository = JobRepository(conn)
        repository.create(job_id="li-review-0001", platform=Platform.LINKEDIN, mode=PublishMode.REAL, stem="post",
                          media_name="post.jpg", caption_name="post.txt", media_sha256="b" * 64,
                          caption_sha256="c" * 64, status=JobStatus.PUBLISHING)
        repository.update("li-review-0001", status=JobStatus.NEEDS_REVIEW)
    finally:
        conn.close()

    assert main("resolve", "li-review", "--failed", "--platform", "instagram") == 1
    assert "is a LinkedIn job, not Instagram. Nothing was changed." in capsys.readouterr().err
    assert main("resolve", "li-review", "--failed", "--platform", "linkedin") == 0
