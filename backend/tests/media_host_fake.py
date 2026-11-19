"""An in-memory media host for tests (platforms/media_host.py). Nothing leaves the process.

URLs carry a random ``sig`` like a signed URL would, so tests can check that a URL is never logged,
stored or put in an error text. ``fail_on`` names the operations that raise ``MediaHostError``.
"""

import secrets
import uuid

from social_media_auto_poster.platforms.media_host import MediaHostError


class InMemoryMediaHost:
    def __init__(self, fail_on: frozenset[str] | set[str] = frozenset()) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail_on = set(fail_on)
        self.signature = secrets.token_hex(16)

    def put(self, data: bytes, *, content_type: str) -> str:
        self._call("put")
        key = f"ig-tmp/{uuid.uuid4().hex}.jpg"
        self.objects[key] = data
        self.content_types[key] = content_type
        return key

    def url(self, key: str) -> str:
        self._call("url")
        return f"https://media.invalid/{key}?sig={self.signature}"

    def delete(self, key: str) -> None:
        self._call("delete")
        self.objects.pop(key, None)

    def leftovers(self) -> tuple[str, ...]:
        return tuple(self.objects)

    def _call(self, operation: str) -> None:
        self.calls.append(operation)
        if operation in self.fail_on:
            raise MediaHostError(f"{operation} refused")
