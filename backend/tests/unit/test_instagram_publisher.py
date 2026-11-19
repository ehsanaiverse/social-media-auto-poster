"""The Instagram publisher (step C) against a fake Graph API and an in-memory media host. No network.

Every branch of steps a-g in platforms/instagram/publisher.py, the order of the calls (the container ID is
recorded before media_publish), media_publish at most once, the picture deleted or kept as specified, and the
token, the signed media URL and the caption never appearing in any log line, error text, exception or repr.
"""

import logging
from pathlib import Path

import httpx
import pytest

from instagram_fake import CONTAINER_ID, MEDIA_ID, TOKEN, USER_ID, FakeGraph, graph_error, json_response
from media_host_fake import InMemoryMediaHost
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PostContent
from social_media_auto_poster.platforms.base import PublishOutcomeUnknown
from social_media_auto_poster.platforms.instagram.client import InstagramClient, RealInstagramForbidden
from social_media_auto_poster.platforms.instagram.publisher import POLL_ATTEMPTS, POLL_INTERVAL_S, InstagramPublisher

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
CAPTION = "Private caption text that must never be logged #launch"


class Run:
    """One publish attempt and everything it touched."""

    def __init__(self, *, host_fails=frozenset(), recorder_fails=False, caption=CAPTION, media=JPEG, **answers):
        self.graph = FakeGraph()
        self.graph.answers.update(answers)
        self.host = InMemoryMediaHost(fail_on=host_fails)
        self.recorded: list[str] = []
        self.sleeps: list[float] = []
        self._recorder_fails = recorder_fails
        self.post = PostContent(
            PostCandidate(Platform.INSTAGRAM, "post", Path("post.jpg"), Path("post.txt")),
            media_sha256="m" * 64, caption_sha256="c" * 64, media=media, caption=caption,
        )
        client = InstagramClient(TOKEN, user_id=USER_ID, transport=self.graph.transport())
        self.publisher = InstagramPublisher(client, self.host, sleep=self.sleeps.append)
        self.result = None
        self.error: BaseException | None = None

    def record(self, container_id: str) -> None:
        self.graph.events.append("record")
        if self._recorder_fails:
            raise OSError("database is locked")
        self.recorded.append(container_id)

    def go(self):
        try:
            self.result = self.publisher.publish(self.post, record=self.record)
        except PublishOutcomeUnknown as error:
            self.error = error
        return self

    @property
    def status(self) -> JobStatus:
        return JobStatus.NEEDS_REVIEW if self.error is not None else self.result.status

    @property
    def text(self) -> str:
        return str(self.error) if self.error is not None else (self.result.error or "")


def _run(**kwargs) -> Run:
    return Run(**kwargs).go()


# The happy path and the order of calls


def test_a_post_is_published_once_with_the_exact_bytes_and_the_picture_is_deleted():
    run = _run()

    assert run.result.status is JobStatus.PUBLISHED and run.result.platform_post_id == MEDIA_ID
    assert run.graph.events == ["limit", "create", "record", "status", "publish"]
    assert run.recorded == [CONTAINER_ID]
    assert run.host.calls == ["put", "url", "delete"] and run.host.leftovers() == ()
    create = next(r for r in run.graph.requests if r.url.path.endswith(f"/{USER_ID}/media"))
    body = httpx.Request("POST", "https://x", content=create.content).read()
    assert CAPTION.encode() in body and run.host.signature.encode() in body
    for request in run.graph.requests:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)


def test_the_uploaded_bytes_are_exactly_the_hashed_bytes():
    graph_saw = {}
    run = Run()

    def capture(request):
        graph_saw["host"] = dict(run.host.objects)  # What the media host holds when Instagram is asked to fetch it.
        return json_response(200, {"id": CONTAINER_ID})

    run.graph.answers["create"] = capture
    run.go()
    assert list(graph_saw["host"].values()) == [JPEG]
    assert run.host.content_types and set(run.host.content_types.values()) == {"image/jpeg"}


def test_the_container_id_is_recorded_before_media_publish():
    run = _run()
    assert run.graph.events.index("record") < run.graph.events.index("publish")


# 0. Local checks on the exact bytes


def test_a_caption_that_breaks_instagrams_rules_is_failed_before_anything_is_sent():
    run = _run(caption="#t " * 31)
    assert run.status is JobStatus.FAILED and "31 hashtags" in run.text and "nothing was sent" in run.text
    assert run.graph.events == [] and run.host.calls == []


def test_a_png_is_failed_before_anything_is_sent():
    png = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "image-6x3.png").read_bytes()
    run = _run(media=png)
    assert run.status is JobStatus.FAILED and run.graph.events == [] and run.host.calls == []


# a. Publishing quota


@pytest.mark.parametrize("answer", [
    json_response(200, {"data": [{"quota_usage": 50, "config": {"quota_total": 50, "quota_duration": 86400}}]}),
    json_response(200, {"data": [{"quota_usage": 2}]}),
    json_response(200, {"data": []}),
    json_response(200, ["not", "a", "dict"]),
    graph_error(400, 190),
    json_response(500, {}),
    httpx.ConnectError("no route"),
    httpx.ReadTimeout("slow"),
], ids=["reached", "no-config", "empty", "not-a-dict", "token", "500", "connect", "timeout"])
def test_a_reached_or_unreadable_quota_is_failed_and_nothing_is_uploaded(answer):
    run = _run(limit=answer)
    assert run.status is JobStatus.FAILED and "nothing was sent" in run.text
    assert run.graph.events == ["limit"] and run.host.calls == []


def test_the_quota_message_says_how_full_it_is():
    run = _run(limit=json_response(200, {"data": [{"quota_usage": 51, "config": {"quota_total": 50}}]}))
    assert "publishing limit is reached (51 of 50 posts in 24 hours)" in run.text


# b. Media host


def test_a_failed_upload_is_failed_and_no_container_is_created():
    run = _run(host_fails={"put"})
    assert run.status is JobStatus.FAILED and "could not be stored" in run.text
    assert run.graph.events == ["limit"]


def test_a_missing_url_is_failed_and_the_picture_is_deleted():
    run = _run(host_fails={"url"})
    assert run.status is JobStatus.FAILED and run.graph.count("create") == 0 and run.host.leftovers() == ()


# c. Container


@pytest.mark.parametrize("answer", [
    httpx.ConnectError("no route"),
    httpx.ReadTimeout("lost answer"),
    httpx.RemoteProtocolError("dropped"),
    graph_error(400, 36003, 2207009),
    graph_error(400, 9004, 2207052),
    json_response(500, {}),
    json_response(200, {}),
    json_response(200, {"id": "../other"}),
], ids=["connect", "lost-answer", "dropped", "aspect", "fetch", "500", "no-id", "odd-id"])
def test_any_container_failure_is_failed_without_publishing_and_the_picture_is_deleted(answer):
    run = _run(create=answer)
    assert run.status is JobStatus.FAILED and "nothing was published" in run.text
    assert run.graph.count("publish") == 0 and run.recorded == [] and run.host.leftovers() == ()


def test_a_refused_container_keeps_the_code_but_never_the_message():
    run = _run(create=graph_error(400, 36003, 2207009, message="aspect ratio of https://media.invalid/x?sig=abc"))
    assert "Instagram answered 400, code 36003/2207009" in run.text and "media.invalid" not in run.text


# d. Recorder


def test_a_recorder_failure_is_failed_and_media_publish_is_never_called():
    run = _run(recorder_fails=True)
    assert run.status is JobStatus.FAILED and "container ID could not be recorded (OSError)" in run.text
    assert run.graph.events == ["limit", "create", "record"] and run.host.leftovers() == ()


# e. Container status


@pytest.mark.parametrize("status_code", ["ERROR", "EXPIRED"])
def test_an_error_or_expired_container_is_failed(status_code):
    run = _run(status=json_response(200, {"status_code": status_code}))
    assert run.status is JobStatus.FAILED and status_code in run.text
    assert run.graph.count("publish") == 0 and run.host.leftovers() == ()


def test_a_container_that_never_finishes_is_failed_after_bounded_checks_without_real_sleeping():
    run = _run(status=json_response(200, {"status_code": "IN_PROGRESS"}))
    assert run.status is JobStatus.FAILED and "still not ready" in run.text
    assert run.graph.count("status") == POLL_ATTEMPTS
    assert run.sleeps == [POLL_INTERVAL_S] * (POLL_ATTEMPTS - 1)
    assert run.graph.count("publish") == 0 and run.host.leftovers() == ()


def test_a_container_that_finishes_on_a_later_check_is_published():
    run = _run(status=[json_response(200, {"status_code": "IN_PROGRESS"}), json_response(200, {"status_code": "FINISHED"})])
    assert run.status is JobStatus.PUBLISHED and run.graph.count("status") == 2 and run.sleeps == [POLL_INTERVAL_S]


@pytest.mark.parametrize("answer", [json_response(500, {}), httpx.ReadTimeout("slow"), json_response(200, {})],
                         ids=["500", "timeout", "empty"])
def test_an_unreadable_status_is_failed_at_once(answer):
    run = _run(status=answer)
    assert run.status is JobStatus.FAILED and run.graph.count("status") == 1
    assert run.graph.count("publish") == 0 and run.host.leftovers() == ()


def test_an_unknown_status_value_is_polled_then_failed():
    run = _run(status=json_response(200, {"status_code": "SOMETHING_NEW"}))
    assert run.status is JobStatus.FAILED and run.graph.count("status") == POLL_ATTEMPTS
    assert run.graph.count("publish") == 0 and run.host.leftovers() == ()


def test_a_container_already_published_before_media_publish_needs_review_and_keeps_the_picture():
    run = _run(status=json_response(200, {"status_code": "PUBLISHED"}))
    assert run.status is JobStatus.NEEDS_REVIEW and run.graph.count("publish") == 0
    assert len(run.host.leftovers()) == 1


# f. media_publish


def test_a_publish_that_never_connected_is_failed_and_the_picture_is_deleted():
    run = _run(publish=httpx.ConnectError("no route"))
    assert run.status is JobStatus.FAILED and "could not connect" in run.text
    assert run.graph.count("publish") == 1 and run.host.leftovers() == ()


@pytest.mark.parametrize("answer", [
    httpx.ReadTimeout("lost answer"),
    httpx.WriteTimeout("half sent"),
    httpx.RemoteProtocolError("dropped"),
    json_response(500, {}),
    json_response(503, {}),
    json_response(200, {}),
    json_response(200, {"id": ""}),
    httpx.Response(200, content=b"not json"),
    graph_error(400, 24, 2207008),
    graph_error(400, -1, 2207001),
    graph_error(400, 2),
    json_response(302, {}),
    json_response(400, {}),
], ids=["read-timeout", "write-timeout", "dropped", "500", "503", "no-id", "blank-id", "not-json",
        "builder-expired", "server-error", "service", "redirect", "400-no-code"])
def test_an_uncertain_publish_needs_review_is_never_retried_and_keeps_the_picture(answer):
    run = _run(publish=answer)
    assert run.status is JobStatus.NEEDS_REVIEW and "check Instagram before re-posting" in run.text
    assert run.graph.count("publish") == 1
    assert len(run.host.leftovers()) == 1


@pytest.mark.parametrize(("code", "subcode"), [(9, 2207042), (9007, 2207027), (25, 2207050), (4, 2207051), (190, None),
                                               (4, None), (17, None), (80002, None), (10, None), (200, None)])
def test_a_documented_refusal_is_failed_after_one_status_check(code, subcode):
    run = _run(publish=graph_error(400, code, subcode))
    assert run.status is JobStatus.FAILED and "Instagram refused the post" in run.text
    assert run.graph.count("publish") == 1
    assert run.graph.events[-1] == "status"  # One read-only check after the refusal, nothing more.
    assert run.host.leftovers() == ()


def test_a_refusal_whose_container_says_published_needs_review():
    run = _run(publish=graph_error(400, 9, 2207042),
               status=[json_response(200, {"status_code": "FINISHED"}), json_response(200, {"status_code": "PUBLISHED"})])
    assert run.status is JobStatus.NEEDS_REVIEW and run.graph.count("publish") == 1 and len(run.host.leftovers()) == 1


def test_a_partial_success_is_still_published_and_the_issue_is_logged(caplog):
    caplog.set_level(logging.WARNING)
    run = _run(publish=json_response(200, {"id": MEDIA_ID, "config_issue": "CAPTION_NOT_ATTACHED"}))
    assert run.result.status is JobStatus.PUBLISHED
    assert "config_issue=CAPTION_NOT_ATTACHED" in caplog.text


# g. Deleting the picture


def test_a_failed_delete_never_changes_the_outcome_and_is_logged(caplog):
    caplog.set_level(logging.WARNING)
    run = _run(host_fails={"delete"})
    assert run.result.status is JobStatus.PUBLISHED and len(run.host.leftovers()) == 1
    assert "Could not delete the picture from the media host" in caplog.text


def test_no_branch_ever_calls_media_publish_twice():
    scenarios = [{}, {"publish": httpx.ReadTimeout("x")}, {"publish": graph_error(400, 9, 2207042)},
                 {"publish": json_response(500, {})}, {"status": json_response(200, {"status_code": "PUBLISHED"})}]
    for answers in scenarios:
        assert _run(**answers).graph.count("publish") <= 1


# Secrets


SCENARIOS = [
    {}, {"limit": graph_error(400, 190, message=f"token {TOKEN} expired")},
    {"create": graph_error(400, 9004, 2207052, message=f"could not fetch {TOKEN}")},
    {"create": httpx.ReadTimeout(f"lost {TOKEN}")}, {"publish": httpx.ReadTimeout(f"lost {TOKEN}")},
    {"publish": graph_error(400, 190, message=f"bad token {TOKEN}")},
    {"publish": json_response(500, {"error": {"message": TOKEN, "code": 1}})},
    {"status": json_response(200, {"status_code": "ERROR", "status": TOKEN})},
    {"host_fails": {"delete"}}, {"recorder_fails": True},
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_token_the_media_url_and_the_caption_never_reach_a_text(scenario, caplog):
    caplog.set_level(logging.DEBUG)
    run = _run(**scenario)
    texts = [caplog.text, run.text, repr(run.result), repr(run.publisher), repr(run.error),
             repr(run.publisher._client)]
    for text in texts:
        assert TOKEN not in text
        assert run.host.signature not in text
        assert "Private caption text" not in text


def test_an_uncertain_outcome_logged_with_its_traceback_keeps_the_token_out(caplog):
    # The workflow logs an uncertain outcome with logger.exception, which prints the whole traceback.
    caplog.set_level(logging.DEBUG)
    run = _run(publish=httpx.ReadTimeout(f"lost answer, token {TOKEN}"))
    try:
        raise run.error
    except PublishOutcomeUnknown:
        logging.getLogger("test").exception("Publish outcome unknown; marking NEEDS_REVIEW")
    assert "Publish outcome unknown" in caplog.text and TOKEN not in caplog.text


# The client


def test_the_client_refuses_a_real_transport():
    with pytest.raises(RealInstagramForbidden, match="forbidden until D48 is accepted"):
        InstagramClient(TOKEN, user_id=USER_ID, transport=httpx.HTTPTransport(retries=0))


def test_the_client_hides_the_token_and_only_takes_numeric_ids():
    client = InstagramClient(TOKEN, user_id=USER_ID, transport=FakeGraph().transport())
    assert TOKEN not in repr(client) and client.redact(f"a {TOKEN} b") == "a [redacted] b"
    with pytest.raises(ValueError):
        InstagramClient(TOKEN, user_id="../me", transport=FakeGraph().transport())
    with pytest.raises(ValueError):
        client.publish_container("1/../2")
