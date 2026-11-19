"""An S3-compatible media host (D48: Backblaze B2, private bucket), signed with hand-written SigV4 over httpx.

Path-style URLs (``https://<endpoint>/<bucket>/<key>``). ``url`` returns a presigned GET URL valid 1 hour.
Not wired into the real publisher yet: real Instagram stays refused (D49) until the real-Instagram step.

Secrets: the secret access key is used only to sign. It, the signatures, ``Authorization`` headers and
presigned URLs never appear in a repr, a log line or an error text. Errors carry the operation, the HTTP
status and the service's short error code only (its message can echo the request).
"""

import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from social_media_auto_poster.platforms import sigv4
from social_media_auto_poster.platforms.media_host import MediaHostError

SETTING_NAMES = ("MEDIA_S3_ENDPOINT", "MEDIA_S3_REGION", "MEDIA_S3_ACCESS_KEY_ID", "MEDIA_S3_SECRET_ACCESS_KEY", "MEDIA_S3_BUCKET")
URL_LIFETIME_S = 3600  # D48: a presigned URL lives 1 hour.
LEFTOVER_PREFIX = "ig-tmp/"
TIMEOUT_S = 30.0
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,511}$")  # Keys come from new_media_key; nothing needs escaping.
_CODE = re.compile(r"^[A-Za-z0-9.]{1,64}$")
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


@dataclass(frozen=True)
class S3Config:
    endpoint: str  # https://s3.<region>.backblazeb2.com
    region: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)


def load_s3_config(base_dir: Path, environ: Mapping[str, str | None] | None = None) -> S3Config:
    """Read the MEDIA_S3_* settings from ``base_dir/.env`` (real environment variables win). Never mutates os.environ."""
    if environ is None:
        environ = {**dotenv_values(base_dir / ".env"), **os.environ}
    values = {name: (environ.get(name) or "").strip() for name in SETTING_NAMES}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise MediaHostError(f"media host not configured: set {', '.join(missing)} in backend\\.env")
    endpoint = values["MEDIA_S3_ENDPOINT"].rstrip("/")
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname or parts.path or parts.query:
        raise MediaHostError("MEDIA_S3_ENDPOINT must be an https:// address with no path, e.g. https://s3.us-west-004.backblazeb2.com")
    return S3Config(endpoint=endpoint, region=values["MEDIA_S3_REGION"], bucket=values["MEDIA_S3_BUCKET"],
                    access_key_id=values["MEDIA_S3_ACCESS_KEY_ID"], secret_access_key=values["MEDIA_S3_SECRET_ACCESS_KEY"])


class S3MediaHost:
    """``MediaHost`` on an S3-compatible bucket. Every request is sent once (no retries, no redirects)."""

    def __init__(self, config: S3Config, *, transport: httpx.BaseTransport | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._config = config
        self._host = urlsplit(config.endpoint).netloc
        self._now = now
        self._http = httpx.Client(transport=transport or httpx.HTTPTransport(retries=0), timeout=TIMEOUT_S,
                                  follow_redirects=False)

    def __repr__(self) -> str:
        return f"S3MediaHost(endpoint={self._config.endpoint!r}, bucket={self._config.bucket!r})"

    def close(self) -> None:
        self._http.close()

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        response = self._send("upload", "PUT", self._path(key), body=data, headers={"content-type": content_type})
        if response.status_code != 200:
            raise _refused("upload", response)

    def url(self, key: str) -> str:
        """A presigned GET URL, valid URL_LIFETIME_S. A bearer secret: never log or store it."""
        path = self._path(key)
        query = sigv4.presigned_query(
            self._config.access_key_id, self._config.secret_access_key, method="GET", host=self._host, path=path,
            amz_date=self._amz_date(), region=self._config.region, service="s3", expires_s=URL_LIFETIME_S,
        )
        return f"{self._config.endpoint}{path}?{query}"

    def delete(self, key: str) -> None:
        response = self._send("delete", "DELETE", self._path(key))
        if response.status_code not in (200, 204, 404):  # A key that is already gone is not an error.
            raise _refused("delete", response)

    def leftovers(self) -> tuple[str, ...]:
        """Every key under ``ig-tmp/`` still stored (ListObjectsV2, following continuation tokens)."""
        keys: list[str] = []
        token: str | None = None
        while True:
            params = [("list-type", "2"), ("prefix", LEFTOVER_PREFIX)]
            if token:
                params.append(("continuation-token", token))
            response = self._send("list", "GET", f"/{sigv4.uri_encode(self._config.bucket)}", params=params)
            if response.status_code != 200:
                raise _refused("list", response)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                raise MediaHostError("list failed: the answer was not readable") from None
            keys += [element.text or "" for element in root.iter(f"{_NS}Key")]
            truncated = (root.findtext(f"{_NS}IsTruncated") or "").strip().lower() == "true"
            token = root.findtext(f"{_NS}NextContinuationToken")
            if not truncated or not token:
                return tuple(keys)

    # Requests

    def _path(self, key: str) -> str:
        if not _KEY.match(key) or ".." in key:
            raise MediaHostError("refused: the object key is not a plain key")
        return f"/{sigv4.uri_encode(self._config.bucket)}/{sigv4.uri_encode(key, keep_slash=True)}"

    def _amz_date(self) -> str:
        return self._now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    def _send(self, operation: str, method: str, path: str, *, params: list[tuple[str, str]] | None = None,
              body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
        params = params or []
        amz_date = self._amz_date()
        payload_hash = sigv4.sha256_hex(body)
        signed = {"host": self._host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date, **(headers or {})}
        authorization = sigv4.authorization(
            self._config.access_key_id, self._config.secret_access_key, method=method, path=path, params=params,
            headers=signed, payload_hash=payload_hash, amz_date=amz_date, region=self._config.region, service="s3",
        )
        query = sigv4.canonical_query(params)
        url = f"{self._config.endpoint}{path}" + (f"?{query}" if query else "")
        request_headers = {name: value for name, value in signed.items() if name != "host"}
        try:
            return self._http.request(method, url, content=body or None, headers={**request_headers, "authorization": authorization})
        except httpx.HTTPError as error:
            # Only the exception's type: its text can contain the URL.
            raise MediaHostError(f"{operation} failed: {type(error).__name__}") from None


def _refused(operation: str, response: httpx.Response) -> MediaHostError:
    code = None
    try:
        code = (ET.fromstring(response.content).findtext("Code") or "").strip() if response.content else None
    except ET.ParseError:
        pass
    shown = f", {code}" if code and _CODE.match(code) else ""
    return MediaHostError(f"{operation} refused (HTTP {response.status_code}{shown})")
