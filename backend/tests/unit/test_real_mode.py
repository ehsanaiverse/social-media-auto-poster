"""Safeguards checked before a real run (D33). Temporary folders and databases only."""

import pytest

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.real_mode import archive_without_history, posts_only_simulated
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

LINKEDIN = Platform.LINKEDIN
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_dir=tmp_path, data_dir=tmp_path / "data")


@pytest.fixture
def repo(settings):
    conn = open_app_db(settings.app_db_path)
    yield JobRepository(conn)
    conn.close()


def make_post(settings: Settings, stem: str, caption: bytes = b"Hello") -> PostCandidate:
    incoming = settings.incoming_dir(LINKEDIN)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / f"{stem}.jpg").write_bytes(JPEG_BYTES)
    (incoming / f"{stem}.txt").write_bytes(caption)
    return PostCandidate(LINKEDIN, stem, incoming / f"{stem}.jpg", incoming / f"{stem}.txt")


def record(repo: JobRepository, post: PostCandidate, job_id: str, mode: PublishMode, status: JobStatus) -> None:
    repo.create(
        job_id=job_id, platform=LINKEDIN, mode=mode, stem=post.stem, media_name=post.media_path.name,
        caption_name=post.caption_path.name, media_sha256=sha256_file(post.media_path),
        caption_sha256=sha256_file(post.caption_path), status=status,
    )


# posts_only_simulated


def test_a_simulated_post_is_reported(settings, repo):
    post = make_post(settings, "launch")
    record(repo, post, "sim-1", PublishMode.SIMULATED, JobStatus.SIMULATED)

    [found] = posts_only_simulated((post,), repo)

    assert found.candidate == post
    assert found.simulated_job.job_id == "sim-1"


def test_new_content_is_not_reported(settings, repo):
    assert posts_only_simulated((make_post(settings, "launch"),), repo) == []


def test_content_already_handled_in_real_mode_is_not_reported(settings, repo):
    post = make_post(settings, "launch")
    record(repo, post, "sim-1", PublishMode.SIMULATED, JobStatus.SIMULATED)
    record(repo, post, "real-1", PublishMode.REAL, JobStatus.PUBLISHED)

    assert posts_only_simulated((post,), repo) == []  # A real run skips it as a duplicate anyway.


def test_same_content_under_another_name_is_reported(settings, repo):
    original = make_post(settings, "launch")
    record(repo, original, "sim-1", PublishMode.SIMULATED, JobStatus.SIMULATED)
    copy = make_post(settings, "launch-copy")  # Same bytes, other name.

    [found] = posts_only_simulated((copy,), repo)

    assert found.candidate.stem == "launch-copy" and found.simulated_job.job_id == "sim-1"


def test_failed_simulations_and_unreadable_files_are_not_reported(settings, repo):
    failed = make_post(settings, "failed", caption=b"failed once")
    record(repo, failed, "sim-failed", PublishMode.SIMULATED, JobStatus.FAILED)
    missing = make_post(settings, "missing")
    record(repo, missing, "sim-2", PublishMode.SIMULATED, JobStatus.SIMULATED)
    missing.caption_path.unlink()

    assert posts_only_simulated((failed, missing), repo) == []


# archive_without_history


def archive_folder(settings: Settings, name: str):
    folder = settings.archive_dir(LINKEDIN) / name
    folder.mkdir(parents=True)
    return folder


def test_archive_folders_without_any_real_job_are_reported(settings, repo):
    post = make_post(settings, "launch")
    record(repo, post, "sim-1", PublishMode.SIMULATED, JobStatus.SIMULATED)  # Simulated history does not count.
    folder = archive_folder(settings, "20261020T100000Z_old-post")

    assert archive_without_history(settings, repo, LINKEDIN) == [folder]


def test_a_missing_database_with_archive_folders_is_reported(settings):
    folder = archive_folder(settings, "20261020T100000Z_old-post")

    assert archive_without_history(settings, None, LINKEDIN) == [folder]


def test_archive_folders_are_fine_when_real_history_exists(settings, repo):
    post = make_post(settings, "launch")
    record(repo, post, "real-1", PublishMode.REAL, JobStatus.FAILED)  # Any real job shows the history is there.
    archive_folder(settings, "20261020T100000Z_old-post")

    assert archive_without_history(settings, repo, LINKEDIN) == []


def test_an_empty_or_missing_archive_is_fine(settings, repo):
    assert archive_without_history(settings, repo, LINKEDIN) == []
    settings.archive_dir(LINKEDIN).mkdir(parents=True)
    (settings.archive_dir(LINKEDIN) / "stray.txt").write_text("not a post folder", encoding="utf-8")
    assert archive_without_history(settings, repo, LINKEDIN) == []


def test_has_jobs_is_per_mode(settings, repo):
    assert not repo.has_jobs(mode=PublishMode.REAL)
    record(repo, make_post(settings, "launch"), "sim-1", PublishMode.SIMULATED, JobStatus.SIMULATED)

    assert repo.has_jobs(mode=PublishMode.SIMULATED)
    assert not repo.has_jobs(mode=PublishMode.REAL)
