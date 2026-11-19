"""Where a picture is put so a platform can fetch it by URL (Instagram accepts images only by URL, S12/S13).

Only the interface lives here. The real host (Cloudflare R2 or S3, signed GET URLs) is future work,
blocked on D48 and a separate decision about SigV4 or a new dependency; tests use an in-memory fake
(``tests/media_host_fake.py``).

A URL from ``url`` is a bearer secret: anyone holding it can fetch the picture until it expires. It is
never logged, stored, put in an error text or returned by the API.
"""

from typing import Protocol


class MediaHostError(Exception):
    """The media host refused or failed an operation. Its message never contains a URL."""


class MediaHost(Protocol):
    def put(self, data: bytes, *, content_type: str) -> str:
        """Store ``data`` under a new, random key (never the post's name) and return the key."""
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
