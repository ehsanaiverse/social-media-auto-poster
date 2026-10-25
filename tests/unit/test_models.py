"""Tests for shared models, especially the simulated-versus-published rule."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from social_media_auto_poster.models import (
    JobStatus,
    Platform,
    PostCandidate,
    PublishResult,
    ScanResult,
    ValidationResult,
)
from social_media_auto_poster.platforms.simulator import Simulator


def make_candidate() -> PostCandidate:
    return PostCandidate(
        platform=Platform.LINKEDIN,
        stem="product-launch",
        media_path=Path("incoming/product-launch.jpg"),
        caption_path=Path("incoming/product-launch.txt"),
    )


def test_platform_values_match_folder_names():
    assert [p.value for p in Platform] == ["LinkedIn", "Facebook", "Instagram"]


def test_job_status_values_are_plain_strings():
    assert JobStatus.SIMULATED == "SIMULATED"
    assert isinstance(JobStatus.NEEDS_REVIEW.value, str)


# PublishResult: status mapping


def test_simulated_success_leads_to_simulated_status():
    result = PublishResult(success=True, simulated=True)
    assert result.status is JobStatus.SIMULATED


def test_real_success_with_post_id_leads_to_published_status():
    result = PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:1")
    assert result.status is JobStatus.PUBLISHED


@pytest.mark.parametrize("simulated", [True, False])
def test_failure_leads_to_failed_status(simulated):
    result = PublishResult(success=False, simulated=simulated, error="rejected")
    assert result.status is JobStatus.FAILED


# PublishResult: invariants


@pytest.mark.parametrize("success", [True, False])
def test_simulated_result_cannot_carry_platform_post_id(success):
    error = None if success else "rejected"
    with pytest.raises(ValueError, match="simulated"):
        PublishResult(success=success, simulated=True, platform_post_id="urn:li:share:1", error=error)


@pytest.mark.parametrize("post_id", [None, ""])
def test_real_success_requires_platform_post_id(post_id):
    with pytest.raises(ValueError, match="post ID"):
        PublishResult(success=True, simulated=False, platform_post_id=post_id)


def test_success_cannot_carry_error():
    with pytest.raises(ValueError, match="must not carry an error"):
        PublishResult(success=True, simulated=True, error="oops")


@pytest.mark.parametrize("error", [None, "", "   "])
def test_failure_requires_non_empty_error(error):
    with pytest.raises(ValueError, match="non-empty error"):
        PublishResult(success=False, simulated=True, error=error)


def test_publish_result_is_immutable():
    result = PublishResult(success=True, simulated=True)
    with pytest.raises(FrozenInstanceError):
        result.simulated = False


# Simulator


def test_simulator_never_reports_published():
    result = Simulator().publish(object())
    assert result.success is True
    assert result.simulated is True
    assert result.status is JobStatus.SIMULATED
    assert result.platform_post_id is None


# ValidationResult


def test_valid_result_has_no_errors():
    assert ValidationResult(is_valid=True).errors == ()


def test_invalid_result_keeps_all_errors():
    result = ValidationResult(is_valid=False, errors=("empty caption", "unsupported extension"))
    assert result.errors == ("empty caption", "unsupported extension")


def test_valid_result_cannot_carry_errors():
    with pytest.raises(ValueError, match="must not carry errors"):
        ValidationResult(is_valid=True, errors=("empty caption",))


def test_invalid_result_requires_an_error():
    with pytest.raises(ValueError, match="at least one error"):
        ValidationResult(is_valid=False)


# PostCandidate and ScanResult


def test_post_candidate_is_immutable():
    candidate = make_candidate()
    with pytest.raises(FrozenInstanceError):
        candidate.stem = "other"


def test_scan_result_holds_candidates_and_pending_files():
    candidate = make_candidate()
    lone = Path("incoming/draft.png")
    result = ScanResult(candidates=(candidate,), pending=(lone,))
    assert result.candidates == (candidate,)
    assert result.pending == (lone,)


def test_scan_result_is_immutable():
    result = ScanResult(candidates=(), pending=())
    with pytest.raises(FrozenInstanceError):
        result.pending = (Path("x.txt"),)
