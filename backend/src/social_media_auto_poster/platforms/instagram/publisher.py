"""Publish one picture with its caption to Instagram, with definite outcomes (step C; fakes only until D48).

Order (docs/instagram-plan.md section 7, docs/instagram-api-notes.md section 9):

  0. Instagram's caption and picture rules again, on the exact bytes (they were validated already).
  a. the publishing quota (S15): reached, or not readable -> FAILED; nothing was sent.
  b. the picture's media host key is recorded (``record_media``), then the exact hashed bytes are
     uploaded -> a failure of either is FAILED; nothing is uploaded before its key is recorded.
  c. the container (S13): any failure, including a lost answer -> FAILED. Without its ID we never
     publish it, and an unpublished container expires after 24 hours (S13).
  d. the container ID is recorded (``record``) BEFORE media_publish -> a failure is FAILED, and
     media_publish is never called. So "no recorded ID" always means "never published".
  e. the container's status, a few times (S12: about once a minute, at most 5 minutes): ERROR,
     EXPIRED, unreadable or still IN_PROGRESS -> FAILED.
  f. media_publish, exactly once (S14):
       200 with an ID                        -> PUBLISHED
       the connection never opened           -> FAILED (nothing was sent)
       a documented refusal (4xx, S20-S22)   -> one read-only status check: PUBLISHED -> NEEDS_REVIEW,
                                                anything else -> FAILED
       anything else (timeout or dropped connection after sending, 5xx, other 4xx, 200 without an
       ID, an unreadable answer)             -> NEEDS_REVIEW; never retried
  g. the picture is deleted from the media host after PUBLISHED or FAILED, and kept after
     NEEDS_REVIEW (its key stays recorded). After a successful delete the recorded key is cleared.
     A failed delete or clear is logged and never changes the outcome.

Texts keep the HTTP status and Instagram's numeric ``code``/``error_subcode`` only, never its
message (it can echo the image URL or input), and pass through ``redact`` as a last safety net.
Nothing here logs the caption, the token or the media URL.
"""

import logging
import time
from collections.abc import Callable

import httpx

from social_media_auto_poster.models import PostContent, PublishResult
from social_media_auto_poster.platforms.base import MediaKeyRecorder, PublishOutcomeUnknown, ReferenceRecorder
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.rules import caption_problems, media_problems
from social_media_auto_poster.platforms.media_host import MediaHost, new_media_key

logger = logging.getLogger(__name__)

POLL_ATTEMPTS = 5  # S12: "once per minute, for no more than 5 minutes".
POLL_INTERVAL_S = 60.0

# Answers to media_publish that mean "refused, not published" (HTTP 4xx with these Graph error codes).
# S20: 9/2207042 publishing limit reached, 9007/2207027 not ready, 25/2207050 account restricted,
#      4/2207051 restricted as spam; S21: 190 token expired or invalid, 10 and 200-299 permission;
# S22: 4, 17, 32, 613 and 80002 rate limits.
REFUSAL_CODES = frozenset({190, 4, 17, 32, 613, 80002, 10})
REFUSAL_PAIRS = frozenset({(9, 2207042), (9007, 2207027), (25, 2207050)})


class _NotPublished(Exception):
    """A step before media_publish failed: no post can exist."""


class InstagramPublisher:
    """Instagram's publisher. Stores the container ID through ``record`` before publishing (step D wires it)."""

    records_references = True

    def __init__(
        self,
        client: InstagramClient,
        media_host: MediaHost,
        *,
        sleep: Callable[[float], None] = time.sleep,
        poll_attempts: int = POLL_ATTEMPTS,
        poll_interval_s: float = POLL_INTERVAL_S,
    ):
        self._client = client
        self._host = media_host
        self._sleep = sleep
        self._poll_attempts = poll_attempts
        self._poll_interval_s = poll_interval_s

    def __repr__(self) -> str:
        return "InstagramPublisher()"

    def close(self) -> None:
        self._client.close()

    def publish(
        self, post: PostContent, /, *, record: ReferenceRecorder, record_media: MediaKeyRecorder
    ) -> PublishResult:
        problems = [*caption_problems(post.caption), *media_problems(post.media)]
        if problems:
            return _failed(f"{'; '.join(problems)}; nothing was sent")
        try:
            self._check_quota()
        except _NotPublished as error:
            return _failed(f"{error}; nothing was sent")
        key = new_media_key()
        try:
            record_media(key)  # Committed before the upload, so a stored picture can always be found (D50).
        except Exception as error:  # Deliberately broad: never upload what could not be tracked.
            return _failed(f"the picture's storage key could not be recorded ({type(error).__name__}); nothing was sent")
        try:
            self._host.put(key, post.media, content_type="image/jpeg")  # The exact hashed bytes (D33).
        except Exception as error:  # Deliberately broad: nothing can be published without the picture.
            self._delete(key, record_media)  # Anything partly stored goes too.
            return _failed(f"the picture could not be stored for Instagram ({type(error).__name__}); nothing was sent")

        try:
            container_id = self._create_container(key, post.caption)
            try:
                record(container_id)
            except Exception as error:  # Deliberately broad: without a recorded ID we must not publish.
                raise _NotPublished(f"the container ID could not be recorded ({type(error).__name__})") from error
            self._wait_until_finished(container_id)
        except _NotPublished as error:
            self._delete(key, record_media)
            return _failed(f"{error}; nothing was published")

        try:
            result = self._publish_once(container_id)
        except PublishOutcomeUnknown:
            logger.warning("Instagram publish outcome unknown; the picture is kept for the review: container_id=%s",
                           container_id)
            raise
        self._delete(key, record_media)
        return result

    # Steps before media_publish: any failure means no post exists.

    def _check_quota(self) -> None:
        data = self._json(self._call(self._client.publishing_limit, "reading the publishing quota"),
                          "reading the publishing quota")
        entries = data.get("data")
        entry = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
        usage = entry.get("quota_usage")
        total = (entry.get("config") or {}).get("quota_total") if isinstance(entry.get("config"), dict) else None
        if not (_is_int(usage) and _is_int(total)):
            raise _NotPublished("Instagram's publishing quota could not be read")
        if usage >= total:
            raise _NotPublished(f"Instagram's publishing limit is reached ({usage} of {total} posts in 24 hours)")

    def _create_container(self, key: str, caption: str) -> str:
        try:
            image_url = self._host.url(key)
        except Exception as error:  # Deliberately broad, as for put.
            raise _NotPublished(f"no URL for the stored picture ({type(error).__name__})") from error
        response = self._call(lambda: self._client.create_container(image_url, caption), "creating the container")
        container_id = self._json(response, "creating the container").get("id")
        if not (isinstance(container_id, str) and container_id.isdigit()):
            raise _NotPublished("Instagram did not return a usable container ID")
        return container_id

    def _wait_until_finished(self, container_id: str) -> None:
        for attempt in range(self._poll_attempts):
            status = self._json(self._call(lambda: self._client.container_status(container_id),
                                           "checking the container"), "checking the container").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise _NotPublished(f"Instagram reports the container as {status}")
            if status == "PUBLISHED":  # Impossible before media_publish; never guess.
                raise PublishOutcomeUnknown(
                    f"Instagram reports container {container_id} as PUBLISHED before it was published; "
                    "check Instagram before re-posting"
                )
            if attempt + 1 < self._poll_attempts:
                self._sleep(self._poll_interval_s)
        raise _NotPublished(f"the container was still not ready after {self._poll_attempts} checks")

    # media_publish: sent exactly once, never retried.

    def _publish_once(self, container_id: str) -> PublishResult:
        try:
            response = self._client.publish_container(container_id)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            return _failed(f"could not connect to Instagram ({type(error).__name__}); nothing was published")
        except httpx.HTTPError as error:
            raise PublishOutcomeUnknown(self._client.redact(
                f"the connection failed after media_publish was sent ({type(error).__name__}) for container "
                f"{container_id}; check Instagram before re-posting"
            )) from None  # Not chained: the workflow logs the traceback, and httpx's message is not ours.

        status = response.status_code
        codes = _error_codes(response)
        if status == 200:
            media_id = _json_or_empty(response).get("id")
            if isinstance(media_id, str) and media_id.strip():
                issue = _json_or_empty(response).get("config_issue")
                if isinstance(issue, str) and issue.isupper() and len(issue) <= 64:
                    logger.warning("Instagram published the post with an issue: media_id=%s config_issue=%s",
                                   media_id, issue)  # S14: e.g. CAPTION_NOT_ATTACHED; the post exists.
                return PublishResult(success=True, simulated=False, platform_post_id=media_id)
            raise PublishOutcomeUnknown(
                f"Instagram answered 200 without a media ID for container {container_id}; check Instagram before re-posting"
            )
        if 400 <= status < 500 and _is_refusal(codes):
            if self._status_or_none(container_id) == "PUBLISHED":
                raise PublishOutcomeUnknown(self._client.redact(
                    f"Instagram refused media_publish ({status}{_codes_text(codes)}) but reports container "
                    f"{container_id} as PUBLISHED; check Instagram before re-posting"
                ))
            return _failed(self._client.redact(
                f"Instagram refused the post ({status}{_codes_text(codes)}); nothing was published"
            ))
        raise PublishOutcomeUnknown(self._client.redact(
            f"Instagram answered {status}{_codes_text(codes)} to media_publish for container {container_id}; "
            "the post may exist; check Instagram before re-posting"
        ))

    def _status_or_none(self, container_id: str) -> str | None:
        """One read-only status check after a refusal; None if it cannot be read."""
        try:
            response = self._client.container_status(container_id)
        except httpx.HTTPError:
            return None
        status = _json_or_empty(response).get("status_code") if response.status_code == 200 else None
        return status if isinstance(status, str) else None

    # Helpers.

    def _delete(self, key: str, record_media: MediaKeyRecorder) -> None:
        try:
            self._host.delete(key)
        except Exception as error:  # Deliberately broad: the outcome is already decided.
            logger.warning("Could not delete the picture from the media host; left for cleanup: key=%s error=%s",
                           key, type(error).__name__)
            return  # The key stays recorded, so the picture can still be found.
        try:
            record_media(None)
        except Exception as error:  # Deliberately broad: the picture is gone; a stale key is harmless.
            logger.warning("Could not clear the deleted picture's key: key=%s error=%s", key, type(error).__name__)

    def _call(self, request: Callable[[], httpx.Response], doing: str) -> httpx.Response:
        try:
            return request()
        except httpx.HTTPError as error:
            raise _NotPublished(f"{doing} failed ({type(error).__name__})") from error

    def _json(self, response: httpx.Response, doing: str) -> dict:
        if response.status_code != 200:
            raise _NotPublished(self._client.redact(
                f"{doing}: Instagram answered {response.status_code}{_codes_text(_error_codes(response))}"
            ))
        data = _json_or_empty(response)
        if not data:
            raise _NotPublished(f"{doing}: Instagram's answer was not readable")
        return data


def _is_refusal(codes: tuple[int, int | None] | None) -> bool:
    if codes is None:
        return False
    code, subcode = codes
    return code in REFUSAL_CODES or 200 <= code <= 299 or (code, subcode) in REFUSAL_PAIRS


def _error_codes(response: httpx.Response) -> tuple[int, int | None] | None:
    """Instagram's numeric ``code`` and ``error_subcode`` (S20, S21), never its message, which can echo input."""
    error = _json_or_empty(response).get("error")
    if not isinstance(error, dict) or not _is_int(error.get("code")):
        return None
    subcode = error.get("error_subcode")
    return error["code"], subcode if _is_int(subcode) else None


def _codes_text(codes: tuple[int, int | None] | None) -> str:
    if codes is None:
        return ""
    code, subcode = codes
    return f", code {code}" + (f"/{subcode}" if subcode is not None else "")


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _failed(message: str) -> PublishResult:
    return PublishResult(success=False, simulated=False, error=message)
