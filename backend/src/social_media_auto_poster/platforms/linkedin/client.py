"""HTTP calls to LinkedIn's API. One method per call; no decisions, no retries.

Facts and sources: docs/linkedin-api-notes.md. The publisher (publisher.py)
decides what each answer means.

The access token is only ever sent in the Authorization header, and only to
LinkedIn hosts over HTTPS. It is never part of a URL, a log line, an exception
message, or this object's repr.
"""

import logging
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote, urlsplit

import httpx

# The LinkedIn API version sent with every versioned (/rest) call. Versions are supported for
# at least a year. Update this at least once a year, or as soon as LinkedIn announces its sunset
# (deprecation notices appear at the top of the API pages), then run the tests and linkedin-check.
LINKEDIN_VERSION = "202610"

API_BASE = "https://api.linkedin.com"
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# httpx logs every request URL at INFO. Upload URLs carry signed upload credentials, so keep them out of the log.
logging.getLogger("httpx").setLevel(logging.WARNING)


class LinkedInClient:
    def __init__(self, access_token: str, *, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
            follow_redirects=False,
            # No automatic retries, ever: a repeated post creation could publish twice.
            transport=transport if transport is not None else httpx.HTTPTransport(retries=0),
        )

    def __repr__(self) -> str:
        return "LinkedInClient(access_token=<hidden>)"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    def userinfo(self) -> httpx.Response:
        """The authenticated member (OpenID Connect); ``sub`` is the member ID."""
        return self._client.get("/v2/userinfo")

    def initialize_image_upload(self, owner: str) -> httpx.Response:
        return self._rest("POST", "/rest/images", params={"action": "initializeUpload"},
                          json={"initializeUploadRequest": {"owner": owner}})

    def upload_image(self, upload_url: str, data: bytes) -> httpx.Response:
        """PUT the image bytes to the URL LinkedIn returned. Refuses any URL that is not LinkedIn over HTTPS."""
        if not is_linkedin_url(upload_url):
            raise ValueError("the upload URL LinkedIn returned is not a LinkedIn HTTPS address; nothing was sent")
        return self._client.put(upload_url, content=data, headers={"Content-Type": "application/octet-stream"})

    def get_image(self, image_urn: str) -> httpx.Response:
        return self._rest("GET", f"/rest/images/{quote(image_urn, safe='')}")

    def create_post(self, body: dict[str, Any]) -> httpx.Response:
        """POST /rest/posts, exactly once. Callers must never repeat it."""
        return self._rest("POST", "/rest/posts", json=body)

    def _rest(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Linkedin-Version": LINKEDIN_VERSION, "X-Restli-Protocol-Version": "2.0.0"}
        return self._client.request(method, path, headers=headers, **kwargs)


def is_linkedin_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "linkedin.com" or host.endswith(".linkedin.com"))
