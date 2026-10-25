"""Tests for shared models, especially the simulated-versus-published rule."""

import pytest

from social_media_auto_poster.models import JobStatus, Platform, PublishResult
from social_media_auto_poster.platforms.simulator import Simulator


def test_platform_values_match_folder_names():
    assert [p.value for p in Platform] == ["LinkedIn", "Facebook", "Instagram"]


def test_job_status_values_are_plain_strings():
    assert JobStatus.SIMULATED == "SIMULATED"
    assert isinstance(JobStatus.NEEDS_REVIEW.value, str)


def test_simulated_result_leads_to_simulated_status():
    result = PublishResult(simulated=True, message="test")
    assert result.status is JobStatus.SIMULATED


def test_real_result_with_post_id_leads_to_published_status():
    result = PublishResult(simulated=False, message="ok", platform_post_id="urn:li:share:1")
    assert result.status is JobStatus.PUBLISHED


def test_simulated_result_cannot_carry_platform_post_id():
    with pytest.raises(ValueError, match="simulated"):
        PublishResult(simulated=True, message="test", platform_post_id="urn:li:share:1")


@pytest.mark.parametrize("post_id", [None, ""])
def test_real_result_requires_platform_post_id(post_id):
    with pytest.raises(ValueError, match="post ID"):
        PublishResult(simulated=False, message="ok", platform_post_id=post_id)


def test_simulator_never_reports_published():
    result = Simulator().publish(object())
    assert result.simulated is True
    assert result.status is JobStatus.SIMULATED
    assert result.platform_post_id is None
