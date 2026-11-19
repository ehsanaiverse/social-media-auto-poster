"""The ``platform`` parameter of run, scan and the schedule operations (A3a): LinkedIn by default, others refused.

Only LinkedIn has an adapter. Instagram and Facebook must be refused before anything happens: no lock file,
no data folders, no job database.
"""

import pytest

from social_media_auto_poster.config import load_settings
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.operations.platforms import UnsupportedPlatform
from social_media_auto_poster.operations.run import run_once
from social_media_auto_poster.operations.scan import scan_preview
from social_media_auto_poster.operations.schedules import reconfirm_post, schedule_post, unschedule_post
from social_media_auto_poster.storage.database import open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRepository

UNSUPPORTED = [Platform.FACEBOOK]


@pytest.fixture
def settings():
    return load_settings()


def _nothing_was_touched(settings) -> None:
    assert not settings.lock_file.exists()
    assert not settings.app_db_path.exists()
    assert not settings.data_dir.exists()


@pytest.mark.parametrize("platform", UNSUPPORTED)
@pytest.mark.parametrize("mode", list(PublishMode))
def test_a_run_for_an_unsupported_platform_is_refused_before_anything_happens(settings, platform, mode):
    with pytest.raises(UnsupportedPlatform, match=f"{platform} is not supported"):
        run_once(settings, mode, interval_s=0, platform=platform)
    _nothing_was_touched(settings)


@pytest.mark.parametrize("platform", UNSUPPORTED)
def test_a_scheduled_run_for_an_unsupported_platform_is_refused(settings, platform):
    with pytest.raises(UnsupportedPlatform):
        run_once(settings, PublishMode.REAL, interval_s=0, scheduled=True, platform=platform)
    _nothing_was_touched(settings)


@pytest.mark.parametrize("platform", UNSUPPORTED)
def test_a_scan_for_an_unsupported_platform_is_refused(settings, tmp_path, platform):
    folder = tmp_path / "somewhere"
    folder.mkdir()
    with pytest.raises(UnsupportedPlatform):
        scan_preview(settings, folder=folder, mode=PublishMode.SIMULATED, interval_s=0, check_archive=False,
                     platform=platform)


@pytest.mark.parametrize("platform", UNSUPPORTED)
def test_schedule_operations_for_an_unsupported_platform_are_refused(settings, platform):
    with pytest.raises(UnsupportedPlatform):
        schedule_post(settings, "post", "2026-12-01 09:00", real=False, interval_s=0, platform=platform)
    with pytest.raises(UnsupportedPlatform):
        unschedule_post(settings, "post", platform=platform)
    with pytest.raises(UnsupportedPlatform):
        reconfirm_post(settings, "post", real=False, interval_s=0, platform=platform)
    _nothing_was_touched(settings)


def test_linkedin_given_explicitly_runs_like_the_default(settings):
    incoming = settings.incoming_dir(Platform.LINKEDIN)
    incoming.mkdir(parents=True)
    (incoming / "post.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    (incoming / "post.txt").write_text("Hello", encoding="utf-8")

    preview = scan_preview(settings, folder=incoming, mode=PublishMode.SIMULATED, interval_s=0, check_archive=True,
                           platform=Platform.LINKEDIN)
    assert [view.candidate.stem for view in preview.candidates] == ["post"]

    report = run_once(settings, PublishMode.SIMULATED, interval_s=0, platform=Platform.LINKEDIN)
    assert report.exit_code == 0
    assert [line.status for line in report.jobs] == [JobStatus.SIMULATED.value]

    conn = open_app_db_read_only(settings.app_db_path)
    try:
        [job] = JobRepository(conn).list_recent()
    finally:
        conn.close()
    assert job.platform is Platform.LINKEDIN
