"""Platform-neutral contract implemented by platform-specific publishers."""

from typing import Protocol

from social_media_auto_poster.models import PostContent, PublishResult


class Publisher(Protocol):
    """Publish one post and return a definite result.

    ``post`` holds the exact bytes to send, already checked against the hashes
    recorded by the duplicate check; a publisher never reads the files itself.

    Return a ``PublishResult`` only when the outcome is known, including a failed
    one when nothing was sent. If the outcome is unknown (timeout, lost response),
    raise instead: the workflow turns any exception from ``publish`` into
    ``NEEDS_REVIEW`` and never retries it.
    """

    def publish(self, post: PostContent, /) -> PublishResult: ...
