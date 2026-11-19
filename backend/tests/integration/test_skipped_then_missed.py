"""A schedule skipped because its platform was refused, and more than 6 hours late when the platform works again,
becomes MISSED and is never published (D46, D49).

A real scheduled run skips a platform only while another one goes ahead, and real Instagram can never go ahead
(D49). So this test registers a stand-in second platform for the test only (Facebook's folders, a fake adapter
whose publisher records what it would publish). Nothing here reaches a network; no code guard is changed.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings, ensure_data_dirs
from social_media_auto_poster.models import Platform, PublishMode, PublishResult
from social_media_auto_poster.operations import clock as clock_module
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.operations import platforms
from social_media_auto_poster.operations.common import RealPublishingUnavailable
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.schedules import ScheduleRepository

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
DUE = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


class FakePublisher:
    def __init__(self):
        self.published: list[str] = []

    def publish(self, post, /):
        self.published.append(post.candidate.stem)
        return PublishResult(success=True, simulated=False, platform_post_id=f"post-{len(self.published)}")


class StandInAdapter:
    """A second real platform for this test only."""

    platform = Platform.FACEBOOK
    real_available = True
    records_references = False

    def __init__(self):
        self.publisher = FakePublisher()

    def real_publisher(self, settings):
        return self.publisher

    def schedule_warnings(self, settings, publish_at, now):
        return ()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    now = {"now": DUE}
    monkeypatch.setattr(clock_module, "now", lambda: now["now"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    monkeypatch.delenv("AUTOPOSTER_TIMEZONE", raising=False)
    stand_in = StandInAdapter()
    monkeypatch.setitem(platforms.ADAPTERS, Platform.FACEBOOK, stand_in)
    linkedin = {"refused": True, "publisher": FakePublisher()}

    def linkedin_publisher(settings):
        if linkedin["refused"]:
            raise RealPublishingUnavailable("the LinkedIn access token expired")
        return linkedin["publisher"]

    monkeypatch.setattr(linkedin_ops, "_real_publisher", linkedin_publisher)
    settings = Settings(base_dir=tmp_path, data_dir=tmp_path / "data")
    ensure_data_dirs(settings)
    return settings, now, stand_in, linkedin


def _real_schedule(settings, platform, stem, publish_at):
    folder = settings.incoming_dir(platform)
    (folder / f"{stem}.jpg").write_bytes(JPEG)
    (folder / f"{stem}.txt").write_text(f"Hello from {stem}", encoding="utf-8")
    conn = open_app_db(settings.app_db_path)
    try:
        ScheduleRepository(conn).create(
            schedule_id=str(uuid.uuid4()), platform=platform, mode=PublishMode.REAL, stem=stem,
            media_name=f"{stem}.jpg", caption_name=f"{stem}.txt", media_sha256=sha256_file(folder / f"{stem}.jpg"),
            caption_sha256=sha256_file(folder / f"{stem}.txt"), publish_at=publish_at,
        )
    finally:
        conn.close()


def _states(settings):
    conn = open_app_db(settings.app_db_path)
    try:
        return {s.stem: s.status.value for s in ScheduleRepository(conn).list_recent(10)}
    finally:
        conn.close()


def run() -> int:
    return cli.main(["run", "--real", "--scheduled", "--interval", "0"])


def test_a_skipped_schedule_that_is_too_late_when_its_platform_works_again_is_missed_never_published(setup):
    settings, now, stand_in, linkedin = setup
    _real_schedule(settings, Platform.LINKEDIN, "li-post", DUE - timedelta(minutes=30))  # The oldest.
    _real_schedule(settings, Platform.FACEBOOK, "other-post", DUE - timedelta(minutes=10))

    # 1. LinkedIn is refused (expired token): skipped; the other platform goes ahead.
    now["now"] = DUE + timedelta(minutes=5)
    assert run() == 3
    assert _states(settings) == {"li-post": "SCHEDULED", "other-post": "DONE"}
    assert stand_in.publisher.published == ["other-post"] and linkedin["publisher"].published == []

    # 2. Seven hours later the token works again; the LinkedIn post is now more than 6 hours late.
    linkedin["refused"] = False
    now["now"] = DUE + timedelta(hours=7)
    assert run() == 3  # A MISSED schedule counts as a problem (D46).

    assert _states(settings) == {"li-post": "MISSED", "other-post": "DONE"}
    assert linkedin["publisher"].published == []

    # 3. And it is never published afterwards.
    now["now"] = DUE + timedelta(hours=8)
    assert run() == 0
    assert linkedin["publisher"].published == []
