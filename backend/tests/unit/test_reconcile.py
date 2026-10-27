"""Tests for startup reconciliation of jobs left PUBLISHING by a crash. Temporary folders only."""

import pytest

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.services import reconcile
from social_media_auto_poster.services.archive import ERROR_REPORT_NAME, ArchiveError
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.reconcile import (
    REAL_INTERRUPTED,
    SIMULATED_INTERRUPTED,
    reconcile_interrupted_jobs,
)
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN


@pytest.fixture
def settings(tmp_path) -> Settings:
    settings = Settings(base_dir=tmp_path, data_dir=tmp_path / "data")
    settings.incoming_dir(LINKEDIN).mkdir(parents=True)
    return settings


@pytest.fixture
def repo(tmp_path):
    conn = open_app_db(tmp_path / "database" / "app.db")
    yield JobRepository(conn)
    conn.close()


def stuck_job(repo, settings, *, mode, job_id="job-1", stem="launch", status=JobStatus.PUBLISHING, files=True):
    """Write a post into incoming/ and a job row for it, as a crashed run would leave them."""
    incoming = settings.incoming_dir(LINKEDIN)
    media, caption = incoming / f"{stem}.jpg", incoming / f"{stem}.txt"
    media.write_bytes(JPEG_BYTES)
    caption.write_bytes(f"Caption for {stem}".encode())
    repo.create(
        job_id=job_id, platform=LINKEDIN, mode=mode, stem=stem, media_name=media.name, caption_name=caption.name,
        media_sha256=sha256_file(media), caption_sha256=sha256_file(caption), status=status,
    )
    if not files:
        media.unlink()
        caption.unlink()
    return media, caption


def test_nothing_stale_means_nothing_to_do(repo, settings):
    assert reconcile_interrupted_jobs(repo, settings) == []


def test_simulated_publishing_becomes_failed_and_files_stay(repo, settings):
    media, caption = stuck_job(repo, settings, mode=PublishMode.SIMULATED)

    [recovery] = reconcile_interrupted_jobs(repo, settings)

    assert recovery.status is JobStatus.FAILED
    assert recovery.notes == (SIMULATED_INTERRUPTED, "will be processed again now")
    row = repo.get("job-1")
    assert row.status is JobStatus.FAILED
    assert row.errors == (SIMULATED_INTERRUPTED,)
    assert media.exists() and caption.exists()


def test_simulated_recovery_with_files_gone_says_so(repo, settings):
    stuck_job(repo, settings, mode=PublishMode.SIMULATED, files=False)

    [recovery] = reconcile_interrupted_jobs(repo, settings)

    assert recovery.notes[1] == "its files are no longer in incoming/"


def test_real_publishing_becomes_needs_review_and_moves_files_with_report(repo, settings):
    media, caption = stuck_job(repo, settings, mode=PublishMode.REAL)

    [recovery] = reconcile_interrupted_jobs(repo, settings)

    assert recovery.status is JobStatus.NEEDS_REVIEW
    [folder] = settings.failed_dir(LINKEDIN).iterdir()
    assert recovery.destination == str(folder)
    assert sorted(p.name for p in folder.iterdir()) == [ERROR_REPORT_NAME, "launch.jpg", "launch.txt"]
    report = (folder / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "Status: NEEDS_REVIEW" in report
    assert "Check LinkedIn before re-posting" in report
    assert not media.exists() and not caption.exists()
    row = repo.get("job-1")
    assert row.status is JobStatus.NEEDS_REVIEW
    assert row.destination == str(folder)
    assert row.errors[0] == REAL_INTERRUPTED


def test_real_recovery_with_files_gone_only_changes_status(repo, settings):
    stuck_job(repo, settings, mode=PublishMode.REAL, files=False)

    [recovery] = reconcile_interrupted_jobs(repo, settings)

    assert recovery.status is JobStatus.NEEDS_REVIEW
    assert recovery.destination is None
    assert recovery.notes[1] == "files are not in incoming/; nothing was moved"
    assert not settings.failed_dir(LINKEDIN).exists()
    assert repo.get("job-1").status is JobStatus.NEEDS_REVIEW


def test_real_recovery_leaves_files_with_changed_content_alone(repo, settings):
    media, caption = stuck_job(repo, settings, mode=PublishMode.REAL)
    caption.write_bytes(b"The user rewrote this caption after the crash")

    [recovery] = reconcile_interrupted_jobs(repo, settings)

    assert recovery.status is JobStatus.NEEDS_REVIEW
    assert recovery.notes[1] == "files in incoming/ now have different content; left in place"
    assert media.exists() and caption.exists()
    assert "different content" in repo.get("job-1").errors[-1]


def test_real_recovery_move_failure_still_records_needs_review(repo, settings, monkeypatch):
    media, _ = stuck_job(repo, settings, mode=PublishMode.REAL)

    def broken_move(candidate, dest_dir):
        raise ArchiveError("disk full")

    monkeypatch.setattr(reconcile, "archive_post", broken_move)

    [recovery] = reconcile_interrupted_jobs(repo, settings)

    assert recovery.status is JobStatus.NEEDS_REVIEW
    assert recovery.notes[1] == "could not move files to failed/: disk full"
    assert media.exists()
    row = repo.get("job-1")
    assert row.status is JobStatus.NEEDS_REVIEW
    assert row.errors[-1] == "could not move files to failed/: disk full"


def test_status_is_written_before_files_are_touched(repo, settings, monkeypatch):
    stuck_job(repo, settings, mode=PublishMode.REAL)
    seen = []

    def spying_move(candidate, dest_dir):
        seen.append(repo.get("job-1").status)
        raise ArchiveError("stop here")

    monkeypatch.setattr(reconcile, "archive_post", spying_move)

    reconcile_interrupted_jobs(repo, settings)

    assert seen == [JobStatus.NEEDS_REVIEW]


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        (PublishMode.SIMULATED, JobStatus.SIMULATED),
        (PublishMode.SIMULATED, JobStatus.FAILED),
        (PublishMode.REAL, JobStatus.PUBLISHED),
        (PublishMode.REAL, JobStatus.NEEDS_REVIEW),
    ],
)
def test_other_statuses_are_never_touched(repo, settings, mode, status):
    media, _ = stuck_job(repo, settings, mode=mode, status=status)
    before = repo.get("job-1")

    assert reconcile_interrupted_jobs(repo, settings) == []
    assert repo.get("job-1") == before
    assert media.exists()


def test_several_stuck_jobs_are_all_recovered(repo, settings):
    stuck_job(repo, settings, mode=PublishMode.SIMULATED, job_id="sim", stem="one")
    stuck_job(repo, settings, mode=PublishMode.REAL, job_id="real", stem="two")

    recoveries = reconcile_interrupted_jobs(repo, settings)

    assert [(r.job_id, r.status) for r in recoveries] == [("sim", JobStatus.FAILED), ("real", JobStatus.NEEDS_REVIEW)]
    assert repo.list_by_status(JobStatus.PUBLISHING) == []
