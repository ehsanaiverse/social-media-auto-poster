"""Tests for the job repository and the database's own safety rules. Temporary files only."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import DuplicateJobError, JobNotFoundError, JobRepository

T0 = datetime(2026, 10, 26, 9, 15, 0, tzinfo=UTC)
MEDIA = "a" * 64
CAPTION = "b" * 64
SIM = PublishMode.SIMULATED
REAL = PublishMode.REAL


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def conn(tmp_path):
    connection = open_app_db(tmp_path / "app.db")
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def repo(conn, clock):
    return JobRepository(conn, now=clock)


def create(repo, job_id="job-1", *, mode=SIM, status=JobStatus.SIMULATED, media=MEDIA, caption=CAPTION,
           platform=Platform.LINKEDIN, errors=()):
    return repo.create(
        job_id=job_id,
        platform=platform,
        mode=mode,
        stem="launch",
        media_name="launch.jpg",
        caption_name="launch.txt",
        media_sha256=media,
        caption_sha256=caption,
        status=status,
        errors=errors,
    )


# Basic operations


def test_create_and_get_round_trip(repo):
    created = create(repo, errors=("first", "second"))

    assert repo.get("job-1") == created
    assert created.platform is Platform.LINKEDIN
    assert created.mode is SIM
    assert created.status is JobStatus.SIMULATED
    assert created.errors == ("first", "second")
    assert created.created_at == created.updated_at == T0
    assert created.platform_post_id is None
    assert created.destination is None


def test_get_unknown_returns_none(repo):
    assert repo.get("nope") is None


def test_update_changes_only_given_fields_and_updated_at(repo, clock):
    create(repo, mode=REAL, status=JobStatus.PUBLISHING)
    clock.now = T0 + timedelta(minutes=5)

    updated = repo.update("job-1", status=JobStatus.PUBLISHED, platform_post_id="urn:li:share:1")

    assert updated.status is JobStatus.PUBLISHED
    assert updated.platform_post_id == "urn:li:share:1"
    assert updated.destination is None
    assert updated.errors == ()
    assert updated.created_at == T0
    assert updated.updated_at == T0 + timedelta(minutes=5)


def test_update_can_set_a_field_back_to_none(repo):
    create(repo, status=JobStatus.FAILED)
    repo.update("job-1", destination="failed/x")

    assert repo.update("job-1", destination=None).destination is None


def test_update_errors(repo):
    create(repo, status=JobStatus.FAILED)

    assert repo.update("job-1", errors=("disk full",)).errors == ("disk full",)


def test_update_unknown_job_raises(repo):
    with pytest.raises(JobNotFoundError):
        repo.update("nope", status=JobStatus.FAILED)


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "app.db"
    first = open_app_db(path)
    create(JobRepository(first))
    first.close()

    second = open_app_db(path)
    assert JobRepository(second).get("job-1").status is JobStatus.SIMULATED
    second.close()


# Duplicate lookup


def find(repo, *, mode=SIM, media=MEDIA, caption=CAPTION, platform=Platform.LINKEDIN):
    return repo.find_blocking_duplicate(platform=platform, mode=mode, media_sha256=media, caption_sha256=caption)


def test_finds_live_job_with_same_content_and_mode(repo):
    create(repo)

    assert find(repo).job_id == "job-1"


@pytest.mark.parametrize(
    "kwargs",
    [{"mode": REAL}, {"platform": Platform.FACEBOOK}, {"caption": "c" * 64}, {"media": "d" * 64}],
    ids=["other-mode", "other-platform", "other-caption", "other-media"],
)
def test_does_not_match_different_mode_platform_or_content(repo, kwargs):
    create(repo)

    assert find(repo, **kwargs) is None


def test_failed_job_does_not_block(repo):
    create(repo, status=JobStatus.FAILED)

    assert find(repo) is None


@pytest.mark.parametrize("status", [JobStatus.PUBLISHING, JobStatus.PUBLISHED, JobStatus.NEEDS_REVIEW])
def test_real_live_statuses_block(repo, status):
    create(repo, mode=REAL, status=status)

    assert find(repo, mode=REAL).status is status


# Rules enforced by the database itself


def test_second_live_job_for_same_content_is_rejected(repo):
    create(repo, "job-1")

    with pytest.raises(DuplicateJobError):
        create(repo, "job-2")
    assert repo.get("job-2") is None


def test_live_job_rejected_even_when_first_is_still_publishing(repo):
    create(repo, "job-1", mode=REAL, status=JobStatus.PUBLISHING)

    with pytest.raises(DuplicateJobError):
        create(repo, "job-2", mode=REAL, status=JobStatus.PUBLISHING)


def test_failed_jobs_with_same_content_are_allowed(repo):
    create(repo, "job-1", status=JobStatus.FAILED)
    create(repo, "job-2", status=JobStatus.FAILED)
    create(repo, "job-3", status=JobStatus.SIMULATED)  # A fixed retry after failures.

    assert find(repo).job_id == "job-3"


def test_same_content_in_other_mode_is_allowed(repo):
    create(repo, "job-1", mode=SIM, status=JobStatus.SIMULATED)

    create(repo, "job-2", mode=REAL, status=JobStatus.PUBLISHING)  # Simulated history never blocks a real publish.


def test_reusing_a_job_id_is_not_reported_as_duplicate_content(repo):
    create(repo, "job-1")

    with pytest.raises(sqlite3.IntegrityError) as exc:
        create(repo, "job-1", media="e" * 64)
    assert not isinstance(exc.value.__cause__, DuplicateJobError)


def test_simulated_job_can_never_be_published(repo):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        create(repo, mode=SIM, status=JobStatus.PUBLISHED)


def test_simulated_job_cannot_be_updated_to_published(repo):
    create(repo, mode=SIM, status=JobStatus.SIMULATED)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        repo.update("job-1", status=JobStatus.PUBLISHED)
    assert repo.get("job-1").status is JobStatus.SIMULATED


def test_real_job_can_never_be_simulated(repo):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        create(repo, mode=REAL, status=JobStatus.SIMULATED)


@pytest.mark.parametrize("missing", ["media", "caption"])
def test_live_job_requires_both_hashes(repo, missing):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        create(repo, **{missing: None})


def test_failed_job_may_lack_hashes(repo):
    created = create(repo, status=JobStatus.FAILED, media=None, caption=None)

    assert created.media_sha256 is None


def test_unknown_status_is_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"), conn:
        conn.execute(
            "INSERT INTO jobs (job_id, platform, mode, stem, media_name, caption_name, status, created_at, updated_at)"
            " VALUES ('x', 'LinkedIn', 'simulated', 's', 'm', 'c', 'BOGUS', 't', 't')"
        )


# History


def test_list_recent_is_newest_first_and_limited(repo, clock):
    for minute, job_id in enumerate(["old", "middle", "new"]):
        clock.now = T0 + timedelta(minutes=minute)
        create(repo, job_id, status=JobStatus.FAILED)

    assert [r.job_id for r in repo.list_recent(2)] == ["new", "middle"]


def test_list_recent_same_second_uses_insertion_order(repo):
    create(repo, "first", status=JobStatus.FAILED)
    create(repo, "second", status=JobStatus.FAILED)

    assert [r.job_id for r in repo.list_recent()] == ["second", "first"]


def test_list_recent_empty(repo):
    assert repo.list_recent() == []


def test_list_by_status_returns_only_that_status_oldest_first(repo, clock):
    for minute, (job_id, status) in enumerate([("a", JobStatus.FAILED), ("b", JobStatus.SIMULATED), ("c", JobStatus.FAILED)]):
        clock.now = T0 + timedelta(minutes=minute)
        create(repo, job_id, status=status, caption=f"{minute}" * 64)

    assert [r.job_id for r in repo.list_by_status(JobStatus.FAILED)] == ["a", "c"]
    assert repo.list_by_status(JobStatus.PUBLISHING) == []


def test_list_recent_can_filter_by_status(repo, clock):
    for minute, (job_id, status) in enumerate([("a", JobStatus.FAILED), ("b", JobStatus.SIMULATED), ("c", JobStatus.FAILED)]):
        clock.now = T0 + timedelta(minutes=minute)
        create(repo, job_id, status=status, caption=f"{minute}" * 64)

    assert [r.job_id for r in repo.list_recent(status=JobStatus.FAILED)] == ["c", "a"]
    assert [r.job_id for r in repo.list_recent(1, status=JobStatus.FAILED)] == ["c"]


def test_find_by_id_prefix_matches_start_only_and_has_no_wildcards(repo):
    create(repo, "abc111", status=JobStatus.FAILED)
    create(repo, "abc222", status=JobStatus.FAILED)
    create(repo, "xabc33", status=JobStatus.FAILED)

    assert sorted(r.job_id for r in repo.find_by_id_prefix("abc")) == ["abc111", "abc222"]
    assert [r.job_id for r in repo.find_by_id_prefix("abc2")] == ["abc222"]
    assert repo.find_by_id_prefix("%") == []
    assert repo.find_by_id_prefix("_bc111") == []


# Manual resolution


@pytest.mark.parametrize("status", [JobStatus.PUBLISHED, JobStatus.FAILED])
def test_resolve_needs_review_sets_status_and_appends_note(repo, clock, status):
    create(repo, mode=REAL, status=JobStatus.NEEDS_REVIEW, errors=("outcome unknown",))
    clock.now = T0 + timedelta(hours=1)
    post_id = "urn:li:share:1" if status is JobStatus.PUBLISHED else None

    record = repo.resolve_needs_review("job-1", status=status, note="resolved", platform_post_id=post_id)

    assert record.status is status
    assert record.platform_post_id == post_id
    assert record.errors == ("outcome unknown", "resolved")
    assert record.updated_at == T0 + timedelta(hours=1)


@pytest.mark.parametrize(
    ("mode", "status"),
    [(SIM, JobStatus.SIMULATED), (REAL, JobStatus.PUBLISHED), (REAL, JobStatus.PUBLISHING), (REAL, JobStatus.FAILED)],
)
def test_resolve_needs_review_refuses_other_statuses(repo, mode, status):
    create(repo, mode=mode, status=status)
    before = repo.get("job-1")

    with pytest.raises(JobNotFoundError):
        repo.resolve_needs_review("job-1", status=JobStatus.FAILED, note="x")
    assert repo.get("job-1") == before


def test_resolve_needs_review_only_allows_published_or_failed(repo):
    create(repo, mode=REAL, status=JobStatus.NEEDS_REVIEW)

    with pytest.raises(ValueError):
        repo.resolve_needs_review("job-1", status=JobStatus.SIMULATED, note="x")


def test_resolve_needs_review_simulated_job_cannot_become_published(repo):
    create(repo, mode=SIM, status=JobStatus.NEEDS_REVIEW)

    with pytest.raises(sqlite3.IntegrityError):
        repo.resolve_needs_review("job-1", status=JobStatus.PUBLISHED, note="x", platform_post_id="p")
    assert repo.get("job-1").status is JobStatus.NEEDS_REVIEW
