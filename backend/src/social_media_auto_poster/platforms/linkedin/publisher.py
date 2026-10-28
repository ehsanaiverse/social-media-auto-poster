"""Publish one post to the member's own LinkedIn profile, with definite outcomes.

Steps: check caption and image locally, find the member (``/v2/userinfo``), register
the image upload, PUT the bytes, wait until LinkedIn has processed the image, then
create the post with ``POST /rest/posts``, once.

What each outcome means:

- Anything that goes wrong **before** ``POST /rest/posts`` is a definite FAILED: no
  post exists. An uploaded image that is never used in a post is visible to no one.
- ``POST /rest/posts``:
  - ``201`` with ``x-restli-id``: PUBLISHED with that post ID.
  - ``ConnectError`` / ``ConnectTimeout``: the request never left; FAILED.
  - 400, 401, 403, 404, 422, 429: LinkedIn refused it; FAILED (401: renew the token).
  - Anything else (``ReadTimeout``, ``WriteTimeout``, ``RemoteProtocolError``, a dropped
    connection, 5xx, 409, ``201`` without ``x-restli-id``, an unexpected status): the
    post may exist. ``PublishOutcomeUnknown`` is raised; the workflow records
    NEEDS_REVIEW and never retries.

LinkedIn's error messages are not kept, only the status and its error code: a
refusal can echo the submitted value, which may be the caption.
"""

import time
from collections.abc import Callable

import httpx

from social_media_auto_poster.models import PostContent, PublishResult
from social_media_auto_poster.platforms.linkedin.caption import caption_problem, escape_caption
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient
from social_media_auto_poster.platforms.linkedin.image_size import image_problem

TOKEN_REJECTED = (
    "LinkedIn rejected the access token (401): it has expired or is invalid. "
    "Renew LINKEDIN_ACCESS_TOKEN in backend\\.env"
)
REFUSED_STATUSES = frozenset({400, 401, 403, 404, 422, 429})
IMAGE_POLL_ATTEMPTS = 15
IMAGE_POLL_INTERVAL_S = 2.0


class PublishOutcomeUnknown(Exception):
    """The post may or may not exist on LinkedIn. Recorded as NEEDS_REVIEW; never retried."""


class _NotPosted(Exception):
    """A step before the post failed, so no post exists."""


class LinkedInPublisher:
    def __init__(
        self,
        client: LinkedInClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        poll_attempts: int = IMAGE_POLL_ATTEMPTS,
        poll_interval_s: float = IMAGE_POLL_INTERVAL_S,
    ):
        self._client = client
        self._sleep = sleep
        self._poll_attempts = poll_attempts
        self._poll_interval_s = poll_interval_s
        self._author: str | None = None

    def __repr__(self) -> str:
        return "LinkedInPublisher()"

    def publish(self, post: PostContent, /) -> PublishResult:
        problem = caption_problem(post.caption) or image_problem(post.media)
        if problem is not None:
            return _failed(f"{problem}; nothing was sent")
        try:
            author = self._member()
            image_urn, upload_url = self._register_image(author)
            self._upload(upload_url, post.media)
            self._wait_until_processed(image_urn)
        except _NotPosted as error:
            return _failed(f"{error}; nothing was posted")
        return self._create_post(author, image_urn, escape_caption(post.caption))

    # Steps before the post: any failure means no post exists.

    def _member(self) -> str:
        if self._author is None:
            data = self._json(self._call(self._client.userinfo, "reading the LinkedIn member"), "reading the member")
            member_id = data.get("sub")
            if not isinstance(member_id, str) or not member_id:
                raise _NotPosted("LinkedIn did not return a member ID")
            self._author = f"urn:li:person:{member_id}"
        return self._author

    def _register_image(self, author: str) -> tuple[str, str]:
        response = self._call(lambda: self._client.initialize_image_upload(author), "registering the image upload")
        value = self._json(response, "registering the image upload").get("value") or {}
        image_urn, upload_url = value.get("image"), value.get("uploadUrl")
        if not (isinstance(image_urn, str) and image_urn and isinstance(upload_url, str) and upload_url):
            raise _NotPosted("LinkedIn did not return an image ID and upload address")
        return image_urn, upload_url

    def _upload(self, upload_url: str, data: bytes) -> None:
        try:
            response = self._client.upload_image(upload_url, data)
        except ValueError as error:
            raise _NotPosted(str(error)) from error
        except httpx.HTTPError as error:
            raise _NotPosted(f"uploading the image failed ({type(error).__name__})") from error
        if not response.is_success:
            raise _NotPosted(_refusal("uploading the image", response))

    def _wait_until_processed(self, image_urn: str) -> None:
        """LinkedIn processes uploads after the fact; a post made before that would not be visible."""
        for attempt in range(self._poll_attempts):
            response = self._call(lambda: self._client.get_image(image_urn), "checking the image")
            if response.status_code == 403:
                raise _NotPosted(
                    "LinkedIn does not let this token read the image's processing status (403); "
                    "run linkedin-check"
                )
            status = self._json(response, "checking the image").get("status")
            if status == "AVAILABLE":
                return
            if status == "PROCESSING_FAILED":
                raise _NotPosted("LinkedIn could not process the image")
            if attempt + 1 < self._poll_attempts:
                self._sleep(self._poll_interval_s)
        waited = self._poll_interval_s * (self._poll_attempts - 1)
        raise _NotPosted(f"LinkedIn was still processing the image after {waited:.0f} s")

    def _call(self, request: Callable[[], httpx.Response], doing: str) -> httpx.Response:
        try:
            return request()
        except httpx.HTTPError as error:
            raise _NotPosted(f"{doing} failed ({type(error).__name__})") from error

    def _json(self, response: httpx.Response, doing: str) -> dict:
        if response.status_code == 401:
            raise _NotPosted(TOKEN_REJECTED)
        if not response.is_success:
            raise _NotPosted(_refusal(doing, response))
        try:
            data = response.json()
        except ValueError as error:
            raise _NotPosted(f"{doing}: LinkedIn's answer was not readable") from error
        if not isinstance(data, dict):
            raise _NotPosted(f"{doing}: LinkedIn's answer was not readable")
        return data

    # The post itself: sent exactly once.

    def _create_post(self, author: str, image_urn: str, commentary: str) -> PublishResult:
        body = {
            "author": author,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "content": {"media": {"id": image_urn}},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        try:
            response = self._client.create_post(body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            return _failed(f"could not connect to LinkedIn ({type(error).__name__}); nothing was sent")
        except httpx.HTTPError as error:
            raise PublishOutcomeUnknown(
                f"the connection failed after the post was sent ({type(error).__name__}); "
                "check LinkedIn before re-posting"
            ) from error

        status = response.status_code
        if status == 201:
            post_id = response.headers.get("x-restli-id", "").strip()
            if post_id:
                return PublishResult(success=True, simulated=False, platform_post_id=post_id)
            raise PublishOutcomeUnknown("LinkedIn answered 201 without a post ID; check LinkedIn before re-posting")
        if status == 401:
            return _failed(f"{TOKEN_REJECTED}; nothing was posted")
        if status in REFUSED_STATUSES:
            return _failed(f"{_refusal('LinkedIn refused the post', response)}; nothing was posted")
        raise PublishOutcomeUnknown(
            f"LinkedIn answered {status}{_error_code(response)}; the post may exist; check LinkedIn before re-posting"
        )


def _failed(message: str) -> PublishResult:
    return PublishResult(success=False, simulated=False, error=message)


def _refusal(doing: str, response: httpx.Response) -> str:
    if response.status_code == 429:
        return f"{doing}: LinkedIn's rate limit was reached (429); try again later"
    return f"{doing}: LinkedIn answered {response.status_code}{_error_code(response)}"


def _error_code(response: httpx.Response) -> str:
    """LinkedIn's error code (for example FIELD_LENGTH_TOO_LONG), never its message, which can echo input."""
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return ""
    return f" {code}" if isinstance(code, str) and code.replace("_", "").isalnum() else ""
