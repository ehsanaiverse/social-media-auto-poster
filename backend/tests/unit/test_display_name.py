"""User-facing texts name the platform through ``platforms.rules.display_name`` (A3b)."""

from datetime import UTC, datetime

from social_media_auto_poster.models import JobStatus, Platform
from social_media_auto_poster.platforms.rules import display_name
from social_media_auto_poster.services.archive import write_error_report


def test_display_name_comes_from_the_rules_or_falls_back_to_the_folder_name():
    assert display_name(Platform.LINKEDIN) == "LinkedIn"
    assert display_name(Platform.FACEBOOK) == "Facebook"  # No rules: its folder name.


def test_the_error_report_names_the_jobs_platform(tmp_path):
    common = dict(job_id="j1", stem="post", status=JobStatus.NEEDS_REVIEW, errors=["x"],
                  now=datetime(2026, 11, 19, 12, 0, tzinfo=UTC))  # Fixed, so both reports have the same time line.

    default = write_error_report(tmp_path, **common).read_text(encoding="utf-8")
    assert "Check LinkedIn before re-posting." in default

    named = write_error_report(tmp_path, **common, platform=Platform.FACEBOOK).read_text(encoding="utf-8")
    assert "Check Facebook before re-posting." in named
    assert named.replace("Facebook", "LinkedIn") == default
