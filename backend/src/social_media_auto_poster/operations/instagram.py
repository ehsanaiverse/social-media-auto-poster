"""Instagram's adapter (D48): real publishing through graph.facebook.com and the S3-compatible media host.

Real mode is still chosen only by ``--real`` (CLI) or the typed phrase ``publish to Instagram`` (API); nothing
in ``.env`` or any setting selects it. The System User token (``INSTAGRAM_ACCESS_TOKEN``) never expires and is
never refreshed; it is checked once, read-only, before a real run changes anything. The token and the media
host's keys come from ``backend/.env`` (real environment variables win) and never appear in output.

Every network object is built through ``_transport()``, so tests replace that one function with a fake.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
from dotenv import dotenv_values

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations.common import RealPublishingUnavailable
from social_media_auto_poster.platforms.base import Publisher, RemoteChecker
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.publisher import InstagramPublisher, json_or_empty
from social_media_auto_poster.platforms.instagram.remote import InstagramRemoteChecker
from social_media_auto_poster.platforms.media_host import MediaHost, MediaHostError
from social_media_auto_poster.platforms.s3_media_host import S3MediaHost, load_s3_config

TOKEN_VAR = "INSTAGRAM_ACCESS_TOKEN"
USER_ID_VAR = "INSTAGRAM_USER_ID"
CONFIRMATION_PHRASE = "publish to Instagram"
# A tiny JPEG for the media host round trip of instagram-check. It is stored, fetched and deleted, never posted.
CHECK_IMAGE = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


class InstagramAdapter:
    """Instagram in the platform registry. Each method calls this module's function by name, so tests reach it."""

    platform = Platform.INSTAGRAM
    real_available = True  # D48; --real or the typed phrase still decides.
    records_references = True  # The container ID, before media_publish (D1, D50).
    confirmation_phrase = CONFIRMATION_PHRASE

    def real_publisher(self, settings: Settings) -> Publisher:
        return _real_publisher(settings)

    def schedule_warnings(self, settings: Settings, publish_at: datetime, now: datetime) -> tuple[str, ...]:
        return ()  # The System User token never expires (D48).

    def remote_checker(self, settings: Settings) -> RemoteChecker:
        return _remote_checker(settings)

    def media_host(self, settings: Settings) -> MediaHost:
        return _media_host(settings)


ADAPTER = InstagramAdapter()


def _transport() -> httpx.BaseTransport:
    """The real network, sent once: no retries. Tests replace this function."""
    return httpx.HTTPTransport(retries=0)


def _credentials(settings: Settings) -> tuple[str, str]:
    values = {**dotenv_values(settings.base_dir / ".env"), **os.environ}  # Real environment variables win.
    token, user_id = (values.get(TOKEN_VAR) or "").strip(), (values.get(USER_ID_VAR) or "").strip()
    if not token or not user_id:
        raise RealPublishingUnavailable(f"{TOKEN_VAR} and {USER_ID_VAR} must be set in backend\\.env")
    if not user_id.isdigit():
        raise RealPublishingUnavailable(f"{USER_ID_VAR} must be the Instagram account's numeric ID")
    return token, user_id


def _client(settings: Settings) -> InstagramClient:
    token, user_id = _credentials(settings)
    return InstagramClient(token, user_id=user_id, transport=_transport())


def _account(client: InstagramClient) -> tuple[str, str]:
    """(id, username) of the configured account, read-only. Raises RealPublishingUnavailable if refused."""
    try:
        response = client.account()
    except httpx.HTTPError as error:
        raise RealPublishingUnavailable(f"Instagram could not be reached ({type(error).__name__})") from None
    if response.status_code != 200:
        raise RealPublishingUnavailable(f"Instagram refused the token or the account ID (HTTP {response.status_code})")
    data = json_or_empty(response)
    return str(data.get("id", "")), str(data.get("username", ""))


def _media_host(settings: Settings) -> MediaHost:
    try:
        return S3MediaHost(load_s3_config(settings.base_dir), transport=_transport())
    except MediaHostError as error:
        raise RealPublishingUnavailable(str(error)) from None


def _real_publisher(settings: Settings) -> Publisher:
    """The real publisher, after a read-only token check (as D40 does for LinkedIn)."""
    client = _client(settings)
    try:
        _account(client)
        host = _media_host(settings)
    except RealPublishingUnavailable:
        client.close()
        raise
    return InstagramPublisher(client, host)


def _remote_checker(settings: Settings) -> RemoteChecker:
    """The read-only container-status checker for ``resolve --check`` (D3)."""
    return InstagramRemoteChecker(_client(settings))


TOKEN_TYPE = "System User, no expiry"  # D48: never refreshed.


@dataclass(frozen=True)
class InstagramCheck:
    ok: bool
    lines: tuple[str, ...]
    # The same answers as data, for the API (never the token, a key or a URL).
    username: str | None = None
    account_id: str | None = None
    quota_used: int | None = None
    quota_total: int | None = None
    media_host_ok: bool | None = None

    @property
    def problems(self) -> tuple[str, ...]:
        return tuple(line.removeprefix("  PROBLEM  ") for line in self.lines if line.startswith("  PROBLEM"))


def instagram_configured(settings: Settings) -> bool:
    """Whether the token and the account ID are set in backend/.env. No network."""
    try:
        _credentials(settings)
    except RealPublishingUnavailable:
        return False
    return True


def instagram_check(settings: Settings) -> InstagramCheck:
    """``instagram-check``: the account, the quota and a media host round trip. Read-only on Instagram: never a
    container, never media_publish. Prints no token, key, signature or URL."""
    lines: list[str] = []
    ok = True
    try:
        client = _client(settings)
    except RealPublishingUnavailable as error:
        return InstagramCheck(False, (f"  PROBLEM  {error}",))
    try:
        try:
            account_id, username = _account(client)
            found = {"username": username or None, "account_id": account_id or None}
            lines.append(f"  OK       account {username or '(no username)'} (ID {account_id})")
        except RealPublishingUnavailable as error:
            return InstagramCheck(False, (f"  PROBLEM  {error}",))
        quota_line, used, total = _quota(client)
        lines.append(quota_line)
        ok = used is not None
    finally:
        client.close()
    host_line = _media_round_trip(settings)
    lines.append(host_line)
    host_ok = host_line.startswith("  OK")
    return InstagramCheck(ok and host_ok, tuple(lines), quota_used=used, quota_total=total, media_host_ok=host_ok, **found)


def _quota(client: InstagramClient) -> tuple[str, int | None, int | None]:
    try:
        response = client.publishing_limit()
    except httpx.HTTPError as error:
        return f"  PROBLEM  the publishing quota could not be read ({type(error).__name__})", None, None
    entries = json_or_empty(response).get("data") if response.status_code == 200 else None
    entry = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
    usage, config = entry.get("quota_usage"), entry.get("config")
    total = config.get("quota_total") if isinstance(config, dict) else None
    if not isinstance(usage, int) or not isinstance(total, int):
        return f"  PROBLEM  the publishing quota could not be read (HTTP {response.status_code})", None, None
    return f"  OK       publishing quota: {usage} of {total} posts used in the last 24 hours", usage, total


def _media_round_trip(settings: Settings) -> str:
    """Put a tiny picture, fetch it through its presigned URL as Meta would, then delete it."""
    try:
        host = S3MediaHost(load_s3_config(settings.base_dir), transport=_transport())
    except MediaHostError as error:
        return f"  PROBLEM  {error}"
    key = f"ig-tmp/check-{uuid.uuid4().hex}.jpg"
    result = "  PROBLEM  media host: the round trip did not finish"
    try:
        result = _put_and_fetch(host, key)
    finally:
        try:
            host.delete(key)
        except MediaHostError as error:
            if result.startswith("  OK"):
                result = f"  PROBLEM  media host: the test picture could not be deleted ({error})"
        host.close()
    return result


def _put_and_fetch(host: S3MediaHost, key: str) -> str:
    try:
        host.put(key, CHECK_IMAGE, content_type="image/jpeg")
        try:
            with httpx.Client(transport=_transport(), timeout=30.0, follow_redirects=False) as anonymous:
                fetched = anonymous.get(host.url(key))  # No credentials: only the URL's own signature.
        except httpx.HTTPError as error:
            return f"  PROBLEM  the picture could not be fetched through its presigned URL ({type(error).__name__})"
        if fetched.status_code != 200 or fetched.content != CHECK_IMAGE:
            return f"  PROBLEM  the presigned URL did not return the picture (HTTP {fetched.status_code})"
        return "  OK       media host: upload, fetch by presigned URL, and delete worked"
    except MediaHostError as error:
        return f"  PROBLEM  media host: {error}"
