"""HTTP calls to the Instagram Graph API on graph.facebook.com (D48). One method per call; no decisions, no retries.

Facts and sources: docs/instagram-api-notes.md (source IDs in the comments). The publisher
(publisher.py) and the remote checker (remote.py) decide what each answer means.

The access token is only ever sent in the ``Authorization: Bearer`` header (the content-publishing
guide's own examples do this, S12), only to ``graph.facebook.com`` over HTTPS (the base URL is fixed, redirects are never followed). It is never part of
a URL, a log line, an exception message, or this object's repr.

The token is a System User token (D48): it works on graph.facebook.com, never expires and is never refreshed.
Callers pass a transport without retries (``operations/instagram.py: _transport``); tests pass a fake.
"""

from typing import Any

import httpx

GRAPH_HOST = "https://graph.facebook.com"  # The System User token works here only (D48 spike).
# The Graph API version in every path. v27.0 is the latest on 2026-11-19 (S23); keep it in this one constant.
API_VERSION = "v27.0"
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
REDACTED = "[redacted]"  # Replaces the access token wherever it could appear in a text.


class InstagramClient:
    def __init__(self, access_token: str, *, user_id: str, transport: httpx.BaseTransport):
        if not user_id.isdigit():
            raise ValueError("the Instagram user ID must be digits only")
        self._token = access_token  # Only for redact(); never shown (see __repr__).
        self._user_id = user_id
        self._client = httpx.Client(
            base_url=f"{GRAPH_HOST}/{API_VERSION}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=transport,  # Built without retries: a request is sent at most once.
        )

    def __repr__(self) -> str:
        return "InstagramClient(access_token=<hidden>)"

    def redact(self, text: str) -> str:
        """``text`` with every occurrence of the access token replaced by ``[redacted]``."""
        return text.replace(self._token, REDACTED) if self._token else text

    def close(self) -> None:
        self._client.close()

    def account(self) -> httpx.Response:
        """The configured account's ``id`` and ``username`` (read-only). ``me`` is not the account with this token."""
        return self._client.get(f"/{self._user_id}", params={"fields": "id,username"})

    def publishing_limit(self) -> httpx.Response:
        """The account's publishing quota: ``quota_usage`` and ``config`` (``quota_total``, S15)."""
        return self._client.get(f"/{self._user_id}/content_publishing_limit", params={"fields": "quota_usage,config"})

    def create_container(self, image_url: str, caption: str) -> httpx.Response:
        """``POST /<IG_ID>/media`` with ``image_url`` and ``caption`` (S12, S13). Returns ``{"id": ...}``."""
        return self._client.post(f"/{self._user_id}/media", json={"image_url": image_url, "caption": caption})

    def container_status(self, container_id: str) -> httpx.Response:
        """``GET /<IG_CONTAINER_ID>?fields=status_code`` (S16): EXPIRED, ERROR, FINISHED, IN_PROGRESS, PUBLISHED."""
        return self._client.get(f"/{_digits(container_id)}", params={"fields": "status_code,status"})

    def publish_container(self, container_id: str) -> httpx.Response:
        """``POST /<IG_ID>/media_publish`` with ``creation_id`` (S12, S14). Callers must never repeat it."""
        return self._client.post(f"/{self._user_id}/media_publish", json={"creation_id": _digits(container_id)})

    def recent_media(self, since_unix: int) -> httpx.Response:
        """The account's media created since ``since_unix`` (S13 time-based pagination; fields from S17)."""
        params: dict[str, Any] = {"fields": "id,media_type,timestamp", "since": since_unix}
        return self._client.get(f"/{self._user_id}/media", params=params)


def _digits(identifier: str) -> str:
    """An ID used in a path must be digits only, so an odd answer can never change the request's path."""
    if not identifier.isdigit():
        raise ValueError("an Instagram ID must be digits only")
    return identifier
