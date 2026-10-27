"""Platform-neutral contract implemented by platform-specific publishers."""

from typing import Protocol

from social_media_auto_poster.models import PostCandidate, PublishResult


class Publisher(Protocol):
    """Publish one post and return a definite result.

    Return a ``PublishResult`` only when the outcome is known. If the outcome
    is unknown (timeout, lost response), raise instead: the workflow turns any
    exception from ``publish`` into ``NEEDS_REVIEW`` and never retries it.
    """

    def publish(self, post: PostCandidate, /) -> PublishResult: ...
