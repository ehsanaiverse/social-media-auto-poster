"""The Instagram remote checker (step C): read-only settlement of an uncertain publish, against a fake Graph API."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from instagram_fake import CONTAINER_ID, MEDIA_ID, TOKEN, USER_ID, FakeGraph, json_response
from social_media_auto_poster.models import JobStatus
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.remote import InstagramRemoteChecker

STARTED = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


def _check(container_id=CONTAINER_ID, *, now=STARTED + timedelta(hours=1), **answers):
    graph = FakeGraph()
    graph.answers.update(answers)
    checker = InstagramRemoteChecker(InstagramClient(TOKEN, user_id=USER_ID, transport=graph.transport()),
                                     now=lambda: now)
    outcome = checker.check(container_id, job_created_at=STARTED)
    assert all(request.method == "GET" for request in graph.requests), "the checker must never publish"
    return outcome, graph


def _status(value):
    return json_response(200, {"status_code": value, "id": CONTAINER_ID})


def _media(*items):
    return json_response(200, {"data": list(items)})


def _image(media_id, minutes_after_start=1, media_type="IMAGE"):
    moment = STARTED + timedelta(minutes=minutes_after_start)
    return {"id": media_id, "media_type": media_type, "timestamp": moment.strftime("%Y-%m-%dT%H:%M:%S+0000")}


@pytest.mark.parametrize("container_id", [None, ""])
def test_no_recorded_container_id_means_never_published(container_id):
    outcome, graph = _check(container_id)
    assert outcome.status is JobStatus.FAILED and "media_publish was never called" in outcome.reason
    assert graph.requests == []


def test_published_with_exactly_one_new_picture_gives_its_media_id():
    outcome, graph = _check(status=_status("PUBLISHED"), media=_media(_image(MEDIA_ID)))
    assert outcome.status is JobStatus.PUBLISHED and outcome.post_id == MEDIA_ID
    assert graph.events == ["status", "media"]


@pytest.mark.parametrize("media", [
    _media(),
    _media(_image(MEDIA_ID), _image("17920238422030507")),
    _media(_image(MEDIA_ID, media_type="VIDEO")),
    _media(_image(MEDIA_ID, minutes_after_start=-60)),
    json_response(500, {}),
    httpx.ReadTimeout("slow"),
], ids=["none", "two", "video", "older", "500", "timeout"])
def test_published_without_one_clear_picture_is_published_without_an_id(media):
    outcome, _ = _check(status=_status("PUBLISHED"), media=media)
    assert outcome.status is JobStatus.PUBLISHED and outcome.post_id is None
    assert "could not be identified" in outcome.reason


@pytest.mark.parametrize("value", ["ERROR", "EXPIRED"])
def test_an_error_or_expired_container_was_never_published(value):
    outcome, _ = _check(status=_status(value))
    assert outcome.status is JobStatus.FAILED and "never published" in outcome.reason


@pytest.mark.parametrize("answer", [_status("FINISHED"), _status("IN_PROGRESS"), _status("NEW_VALUE"),
                                    json_response(500, {}), json_response(200, {}), httpx.ReadTimeout("slow"),
                                    httpx.ConnectError("no route")])
def test_anything_else_needs_review(answer):
    outcome, _ = _check(status=answer)
    assert outcome.status is JobStatus.NEEDS_REVIEW and outcome.post_id is None


def test_the_review_note_depends_on_the_jobs_age():
    young, _ = _check(status=_status("FINISHED"), now=STARTED + timedelta(hours=3))
    old, _ = _check(status=_status("FINISHED"), now=STARTED + timedelta(hours=30))
    assert "check again in about 21 hour(s)" in young.reason
    assert "older than 24 hours but not EXPIRED" in old.reason


def test_the_token_never_appears_in_an_outcome():
    outcome, _ = _check(status=json_response(400, {"error": {"message": TOKEN, "code": 190}}))
    assert TOKEN not in outcome.reason and TOKEN not in repr(outcome)
