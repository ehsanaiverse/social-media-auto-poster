"""Migration 3 (D1): ``remote_ref`` and ``media_host_key`` on jobs, from a version-2 database with real-looking rows.

Also ``JobRepository.record_remote_ref``: one committed write, only for a PUBLISHING job without a reference.
"""

import sqlite3
import uuid

import pytest

from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.storage import database
from social_media_auto_poster.storage.database import open_app_db, open_app_db_read_only
from social_media_auto_poster.storage.jobs import COLUMNS, DuplicateJobError, JobRepository, ReferenceNotRecordedError

STAMP = "2026-11-10T09:00:00Z"
V2_BACKUP = "app.db.bak-v2"
PUBLISHED_ID = "41ac71e1-1111-4111-8111-111111111111"
REVIEW_ID = "cc3802b8-2222-4222-8222-222222222222"
SIMULATED_ID = "7cb929ea-3333-4333-8333-333333333333"
FAILED_ID = "9d0e1f2a-4444-4444-8444-444444444444"


def make_v2_database(path) -> None:
    """A database exactly as version 2 left it: four jobs (one a real PUBLISHED LinkedIn post) and a schedule."""
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN; {database.MIGRATIONS[0]} {database.MIGRATIONS[1]} PRAGMA user_version = 2; COMMIT;")
    rows = [
        (PUBLISHED_ID, "real", "first-post", "PUBLISHED", "urn:li:share:7521116341425037312",
         "D:\\data\\LinkedIn\\archive\\20261028T075107Z_first-post", "[]"),
        (REVIEW_ID, "real", "unsure", "NEEDS_REVIEW", None, "D:\\data\\LinkedIn\\failed\\20261029T080000Z_unsure",
         '["publish was interrupted by a crash; the outcome is unknown - check the platform before re-posting"]'),
        (SIMULATED_ID, "simulated", "draft", "SIMULATED", None, None, "[]"),
        (FAILED_ID, "real", "too-long", "FAILED", None, None, '["caption is 3001 characters; LinkedIn allows at most 3000"]'),
    ]
    with conn:
        for index, (job_id, mode, stem, status, post_id, destination, errors) in enumerate(rows):
            conn.execute(
                "INSERT INTO jobs (job_id, platform, mode, stem, media_name, caption_name, media_sha256, caption_sha256,"
                " status, platform_post_id, destination, errors, created_at, updated_at)"
                " VALUES (?, 'LinkedIn', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, mode, stem, f"{stem}.jpg", f"{stem}.txt", f"{index}" * 64, "c" * 64, status, post_id,
                 destination, errors, STAMP, STAMP),
            )
        conn.execute(
            "INSERT INTO schedules (schedule_id, platform, mode, stem, stem_key, media_name, caption_name, media_sha256,"
            " caption_sha256, publish_at, status, job_id, reason, created_at, updated_at)"
            " VALUES (?, 'LinkedIn', 'real', 'first-post', 'first-post', 'first-post.jpg', 'first-post.txt', ?, ?, ?,"
            " 'DONE', ?, NULL, ?, ?)",
            (str(uuid.uuid4()), "0" * 64, "c" * 64, STAMP, PUBLISHED_ID, STAMP, STAMP),
        )
    conn.close()


def v2_rows(path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT {', '.join(COLUMNS)} FROM jobs ORDER BY job_id").fetchall()
    finally:
        conn.close()


def schedules(path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT * FROM schedules").fetchall()
    finally:
        conn.close()


def version(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def backups(folder) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if ".bak-" in p.name)


def test_a_v2_database_is_backed_up_then_migrated_keeping_every_row(tmp_path):
    path = tmp_path / "app.db"
    make_v2_database(path)
    jobs_before, schedules_before = v2_rows(path), schedules(path)

    conn = open_app_db(path)
    records = {record.job_id: record for record in JobRepository(conn).list_recent(10)}
    conn.close()

    assert version(path) == 3
    assert backups(tmp_path) == [V2_BACKUP]
    assert version(tmp_path / V2_BACKUP) == 2
    assert v2_rows(tmp_path / V2_BACKUP) == jobs_before
    assert v2_rows(path) == jobs_before and schedules(path) == schedules_before
    assert len(records) == 4
    assert all(record.remote_ref is None and record.media_host_key is None for record in records.values())
    published = records[PUBLISHED_ID]
    assert published.status is JobStatus.PUBLISHED and published.platform_post_id == "urn:li:share:7521116341425037312"
    assert records[REVIEW_ID].status is JobStatus.NEEDS_REVIEW and records[REVIEW_ID].errors


def test_migrating_is_idempotent_and_never_backs_up_twice(tmp_path):
    path = tmp_path / "app.db"
    make_v2_database(path)
    open_app_db(path).close()
    after_first = v2_rows(path)

    open_app_db(path).close()
    open_app_db(path).close()

    assert version(path) == 3 and backups(tmp_path) == [V2_BACKUP] and v2_rows(path) == after_first


def test_the_published_job_still_blocks_its_content_after_the_migration(tmp_path):
    path = tmp_path / "app.db"
    make_v2_database(path)
    conn = open_app_db(path)
    with pytest.raises(DuplicateJobError):
        JobRepository(conn).create(job_id=str(uuid.uuid4()), platform=Platform.LINKEDIN, mode=PublishMode.REAL,
                                   stem="again", media_name="again.jpg", caption_name="again.txt",
                                   media_sha256="0" * 64, caption_sha256="c" * 64, status=JobStatus.PUBLISHING)
    conn.close()


def test_a_v2_database_read_only_is_read_without_migrating(tmp_path):
    # scan, jobs and schedules open the database read-only: an older one must still be readable.
    path = tmp_path / "app.db"
    make_v2_database(path)

    conn = open_app_db_read_only(path)
    try:
        records = JobRepository(conn).list_recent(10)
    finally:
        conn.close()

    assert len(records) == 4 and all(record.remote_ref is None for record in records)
    assert version(path) == 2 and backups(tmp_path) == []


# record_remote_ref


@pytest.fixture
def repo(tmp_path):
    conn = open_app_db(tmp_path / "app.db")
    yield JobRepository(conn)
    conn.close()


def _job(repo, status=JobStatus.PUBLISHING, platform=Platform.INSTAGRAM) -> str:
    job_id = str(uuid.uuid4())
    repo.create(job_id=job_id, platform=platform, mode=PublishMode.REAL, stem="post", media_name="post.jpg",
                caption_name="post.txt", media_sha256=uuid.uuid4().hex * 2, caption_sha256="c" * 64, status=status)
    return job_id


def test_the_reference_is_committed_for_a_publishing_job(repo, tmp_path):
    job_id = _job(repo)

    repo.record_remote_ref(job_id, "17889455560051444")

    other = sqlite3.connect(tmp_path / "app.db")  # Another connection sees it: it is committed.
    try:
        assert other.execute("SELECT remote_ref FROM jobs WHERE job_id = ?", (job_id,)).fetchone() == ("17889455560051444",)
    finally:
        other.close()
    assert repo.get(job_id).remote_ref == "17889455560051444"


def test_a_reference_is_recorded_once_and_only_for_a_publishing_job(repo):
    job_id = _job(repo)
    repo.record_remote_ref(job_id, "1")
    with pytest.raises(ReferenceNotRecordedError):
        repo.record_remote_ref(job_id, "2")
    assert repo.get(job_id).remote_ref == "1"

    finished = _job(repo, status=JobStatus.FAILED)
    with pytest.raises(ReferenceNotRecordedError):
        repo.record_remote_ref(finished, "3")
    with pytest.raises(ReferenceNotRecordedError):
        repo.record_remote_ref("no-such-job", "4")
    assert repo.get(finished).remote_ref is None
