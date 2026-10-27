"""Tests for `resolve`: manually settling NEEDS_REVIEW jobs. Temporary folders and databases only."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
FIXED_NOW = datetime(2026, 10, 26, 14, 0, 0, tzinfo=UTC)
LINKEDIN = Platform.LINKEDIN


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    monkeypatch.setattr(cli, "_utc_now", lambda: FIXED_NOW)
    return tmp_path


def db(base: Path) -> Path:
    return base / "database" / "app.db"


def seed(base: Path, job_id: str, *, status=JobStatus.NEEDS_REVIEW, mode=PublishMode.REAL, stem="launch",
         caption: bytes = b"Hello", destination: str | None = None) -> None:
    conn = open_app_db(db(base))
    try:
        repo = JobRepository(conn)
        repo.create(
            job_id=job_id, platform=LINKEDIN, mode=mode, stem=stem, media_name=f"{stem}.jpg",
            caption_name=f"{stem}.txt", media_sha256=hashlib.sha256(JPEG_BYTES).hexdigest(),
            caption_sha256=hashlib.sha256(caption).hexdigest(), status=status,
            errors=("publish was interrupted by a crash",),
        )
        if destination:
            repo.update(job_id, destination=destination)
    finally:
        conn.close()


def get(base: Path, job_id: str) -> JobRecord:
    conn = open_app_db(db(base))
    try:
        return JobRepository(conn).get(job_id)
    finally:
        conn.close()


def resolve(*args: str) -> int:
    return cli.main(["resolve", *args])


# Resolving


def test_published_sets_status_post_id_and_keeps_content_blocked(base, capsys):
    seed(base, "abcdef123456", destination=str(base / "data" / "LinkedIn" / "failed" / "20261026T120000Z_launch"))

    assert resolve("abcdef", "--published", "  urn:li:share:999  ") == cli.EXIT_OK

    job = get(base, "abcdef123456")
    assert job.status is JobStatus.PUBLISHED
    assert job.platform_post_id == "urn:li:share:999"
    assert job.errors[-1] == "resolved manually as PUBLISHED (post ID urn:li:share:999) at 2026-10-26T14:00:00Z"
    conn = open_app_db(db(base))
    blocking = JobRepository(conn).find_blocking_duplicate(
        platform=LINKEDIN, mode=PublishMode.REAL, media_sha256=job.media_sha256, caption_sha256=job.caption_sha256
    )
    conn.close()
    assert blocking.job_id == "abcdef123456"
    out = capsys.readouterr().out
    assert "Resolved job abcdef12 (launch) as PUBLISHED." in out
    assert "20261026T120000Z_launch" in out
    assert "stays blocked from re-posting" in out


def test_failed_sets_status_and_allows_a_retry(base, capsys):
    failed_dir = base / "data" / "LinkedIn" / "failed" / "20261026T120000Z_launch"
    failed_dir.mkdir(parents=True)
    (failed_dir / "launch.jpg").write_bytes(JPEG_BYTES)
    (failed_dir / "launch.txt").write_bytes(b"Hello")
    seed(base, "abcdef123456", destination=str(failed_dir))

    assert resolve("abcdef123456", "--failed") == cli.EXIT_OK

    job = get(base, "abcdef123456")
    assert job.status is JobStatus.FAILED
    assert job.platform_post_id is None
    assert job.errors[-1] == "resolved manually as FAILED at 2026-10-26T14:00:00Z"
    out = capsys.readouterr().out
    assert "to retry, fix the post if needed, move launch.jpg and launch.txt back into" in out

    # The user follows the advice; the content is no longer blocked.
    incoming = base / "data" / "LinkedIn" / "incoming"
    incoming.mkdir(parents=True)
    for name in ("launch.jpg", "launch.txt"):
        (failed_dir / name).replace(incoming / name)
    conn = open_app_db(db(base))
    blocking = JobRepository(conn).find_blocking_duplicate(
        platform=LINKEDIN, mode=PublishMode.REAL, media_sha256=job.media_sha256, caption_sha256=job.caption_sha256
    )
    conn.close()
    assert blocking is None
    assert cli.main(["run", "--interval", "0"]) == cli.EXIT_OK
    assert "SIMULATED (not published)" in capsys.readouterr().out


def test_failed_without_moved_files_says_next_run_processes_them(base, capsys):
    seed(base, "abcdef123456")

    resolve("abcdef", "--failed")

    out = capsys.readouterr().out
    assert "were not moved by the app" in out
    assert "the next run will process them again" in out


def test_simulated_job_can_be_failed_but_never_published(base, capsys):
    seed(base, "abcdef123456", mode=PublishMode.SIMULATED)

    assert resolve("abcdef", "--published", "urn:li:share:1") == cli.EXIT_SETUP_ERROR
    assert "simulated job; nothing was sent" in capsys.readouterr().err
    assert get(base, "abcdef123456").status is JobStatus.NEEDS_REVIEW

    assert resolve("abcdef", "--failed") == cli.EXIT_OK
    assert get(base, "abcdef123456").status is JobStatus.FAILED


# Refusals (nothing may change)


@pytest.mark.parametrize(
    ("status", "mode"),
    [
        (JobStatus.SIMULATED, PublishMode.SIMULATED),
        (JobStatus.FAILED, PublishMode.REAL),
        (JobStatus.PUBLISHED, PublishMode.REAL),
        (JobStatus.PUBLISHING, PublishMode.REAL),
    ],
)
def test_only_needs_review_jobs_can_be_resolved(base, capsys, status, mode):
    seed(base, "abcdef123456", status=status, mode=mode)
    before = get(base, "abcdef123456")

    assert resolve("abcdef", "--failed") == cli.EXIT_SETUP_ERROR

    assert f"is {status}; only NEEDS_REVIEW jobs can be resolved" in capsys.readouterr().err
    assert get(base, "abcdef123456") == before


def test_ambiguous_prefix_lists_matches_and_changes_nothing(base, capsys):
    seed(base, "abcdef111111", caption=b"1")
    seed(base, "abcdef222222", caption=b"2")

    assert resolve("abcdef", "--failed") == cli.EXIT_SETUP_ERROR

    err = capsys.readouterr().err
    assert "matches 2 jobs" in err and "abcdef111111" in err and "abcdef222222" in err
    assert get(base, "abcdef111111").status is JobStatus.NEEDS_REVIEW
    assert get(base, "abcdef222222").status is JobStatus.NEEDS_REVIEW


def test_other_rows_are_never_touched(base):
    seed(base, "abcdef111111", caption=b"1")
    seed(base, "fedcba222222", caption=b"2")
    other_before = get(base, "fedcba222222")

    resolve("abcdef111111", "--failed")

    assert get(base, "fedcba222222") == other_before


def test_unknown_job(base, capsys):
    seed(base, "abcdef123456")

    assert resolve("999999", "--failed") == cli.EXIT_SETUP_ERROR
    assert "no job ID starts with '999999'" in capsys.readouterr().err


@pytest.mark.parametrize("post_id", ["", "   "])
def test_empty_post_id_is_refused(base, capsys, post_id):
    seed(base, "abcdef123456")

    assert resolve("abcdef", "--published", post_id) == cli.EXIT_SETUP_ERROR

    assert "must not be empty" in capsys.readouterr().err
    assert get(base, "abcdef123456").status is JobStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "args",
    [["abcde", "--failed"], ["abcdef"], ["abcdef", "--failed", "--published", "x"]],
    ids=["prefix-too-short", "no-outcome", "both-outcomes"],
)
def test_usage_errors(base, args):
    with pytest.raises(SystemExit) as exc:
        resolve(*args)
    assert exc.value.code == 2


def test_lock_held_refuses_and_changes_nothing(base, capsys):
    seed(base, "abcdef123456")

    with single_run_lock(base / "autoposter.lock"):
        assert resolve("abcdef", "--failed") == cli.EXIT_LOCKED

    assert "Another run is in progress" in capsys.readouterr().err
    assert get(base, "abcdef123456").status is JobStatus.NEEDS_REVIEW


def test_no_database_refuses_and_creates_nothing(base, capsys):
    assert resolve("abcdef", "--failed") == cli.EXIT_SETUP_ERROR

    assert "no job database" in capsys.readouterr().err
    assert not (base / "database").exists()


def test_resolution_is_logged_with_run_id(base):
    seed(base, "abcdef123456")

    resolve("abcdef", "--published", "urn:li:share:7")

    [line] = [l for l in (base / "logs" / "autoposter.log").read_text(encoding="utf-8").splitlines() if "resolved" in l]
    assert "job_id=abcdef123456" in line and "mode=real" in line and "status=PUBLISHED" in line
    assert "run_id=-" not in line and "run_id=" in line
