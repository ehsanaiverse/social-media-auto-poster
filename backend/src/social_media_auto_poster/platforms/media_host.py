"""Where a picture is put so a platform can fetch it by URL (Instagram accepts images only by URL, S12/S13).

Only the interface lives here. The real host is ``platforms/s3_media_host.py`` (S3-compatible storage,
Backblaze B2, D48), not used by the publisher yet; tests use an in-memory fake (``tests/media_host_fake.py``).

A URL from ``url`` is a bearer secret: anyone holding it can fetch the picture until it expires. It is
never logged, stored, put in an error text or returned by the API.
"""

import uuid
from typing import Protocol


class MediaHostError(Exception):
    """The media host refused or failed an operation. Its message never contains a URL."""


class MediaHost(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store ``data`` under ``key`` (from ``new_media_key``; recorded before this call, D50)."""
        ...

    def url(self, key: str) -> str:
        """A URL the platform can fetch the object from (signed, short-lived). A bearer secret."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object. Deleting a key that is not there is not an error."""
        ...

    def leftovers(self) -> tuple[str, ...]:
        """Keys still stored: pictures kept for a NEEDS_REVIEW job, or left by a failed delete."""
        ...


def new_media_key(prefix: str = "ig-tmp/") -> str:
    """A new, random object key: never the post's name or caption, so it reveals nothing."""
    return f"{prefix}{uuid.uuid4().hex}.jpg"
