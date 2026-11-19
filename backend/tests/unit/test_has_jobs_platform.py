"""``JobRepository.has_jobs`` is per platform and per mode (A4, D49), and the archive check (D38) uses it per platform.

Rows are inserted with plain SQL, so these tests do not depend on any platform having an adapter: they check
the storage rule itself.
"""

import ast
from pathlib import Path

import pytest

from social_media_auto_poster.config import load_settings
from social_media_auto_poster.models import Platform, PublishMode
from social_media_auto_poster.services.real_mode import archive_without_history
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

BACKEND = Path(__file__).resolve().parents[2]
STAMP = "2026-11-19T12:00:00Z"


@pytest.fixture
def conn(tmp_path):
    connection = open_app_db(tmp_path / "app.db")
    yield connection
    connection.close()


def insert(conn, job_id: str, platform: Platform, mode: PublishMode, status: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO jobs (job_id, platform, mode, stem, media_name, caption_name, media_sha256, caption_sha256,"
            " status, platform_post_id, destination, errors, created_at, updated_at)"
            " VALUES (?, ?, ?, 'post', 'post.jpg', 'post.txt', ?, ?, ?, NULL, NULL, '[]', ?, ?)",
            (job_id, platform.value, mode.value, f"m-{job_id}", f"c-{job_id}", status, STAMP, STAMP),
        )


def test_a_real_linkedin_job_is_not_history_for_another_platform(conn):
    insert(conn, "li-real", Platform.LINKEDIN, PublishMode.REAL, "PUBLISHED")
    repo = JobRepository(conn)

    assert repo.has_jobs(mode=PublishMode.REAL, platform=Platform.LINKEDIN)
    assert not repo.has_jobs(mode=PublishMode.REAL, platform=Platform.INSTAGRAM)
    assert not repo.has_jobs(mode=PublishMode.REAL, platform=Platform.FACEBOOK)


def test_a_real_job_on_another_platform_is_not_linkedin_history(conn):
    insert(conn, "ig-real", Platform.INSTAGRAM, PublishMode.REAL, "FAILED")
    repo = JobRepository(conn)

    assert repo.has_jobs(mode=PublishMode.REAL, platform=Platform.INSTAGRAM)
    assert not repo.has_jobs(mode=PublishMode.REAL, platform=Platform.LINKEDIN)


def test_simulated_history_never_counts_as_real_on_any_platform(conn):
    insert(conn, "li-sim", Platform.LINKEDIN, PublishMode.SIMULATED, "SIMULATED")
    insert(conn, "ig-sim", Platform.INSTAGRAM, PublishMode.SIMULATED, "SIMULATED")
    repo = JobRepository(conn)

    for platform in (Platform.LINKEDIN, Platform.INSTAGRAM):
        assert repo.has_jobs(mode=PublishMode.SIMULATED, platform=platform)
        assert not repo.has_jobs(mode=PublishMode.REAL, platform=platform)
    assert not repo.has_jobs(mode=PublishMode.SIMULATED, platform=Platform.FACEBOOK)


def test_the_archive_check_is_per_platform(conn):
    # D38: post folders in archive/ while the database has no real job means the history may be lost.
    # Another platform's real jobs must not silence that check.
    settings = load_settings()
    for platform in (Platform.LINKEDIN, Platform.INSTAGRAM):
        (settings.archive_dir(platform) / "20261020T100000Z_old-post").mkdir(parents=True)
    insert(conn, "li-real", Platform.LINKEDIN, PublishMode.REAL, "PUBLISHED")
    repo = JobRepository(conn)

    assert archive_without_history(settings, repo, Platform.LINKEDIN) == []
    assert [p.name for p in archive_without_history(settings, repo, Platform.INSTAGRAM)] == ["20261020T100000Z_old-post"]

    insert(conn, "ig-real", Platform.INSTAGRAM, PublishMode.REAL, "FAILED")
    assert archive_without_history(settings, repo, Platform.INSTAGRAM) == []


def _has_jobs_calls(folder: Path):
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "has_jobs":
                yield f"{path.relative_to(BACKEND).as_posix()}:{node.lineno}", node


def test_every_production_caller_passes_the_platform():
    calls = [*_has_jobs_calls(BACKEND / "src"), *_has_jobs_calls(BACKEND / "app")]

    assert calls, "no has_jobs call found: the scan itself may be broken"
    missing = [where for where, node in calls if "platform" not in {kw.arg for kw in node.keywords}]
    assert missing == [], f"has_jobs called without platform= at: {missing}"
