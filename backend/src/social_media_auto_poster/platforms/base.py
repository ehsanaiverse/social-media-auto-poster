"""Platform-neutral contract implemented by platform-specific publishers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from social_media_auto_poster.models import JobStatus, PostContent, PublishResult


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


class PublishOutcomeUnknown(Exception):
    """The post may or may not exist on the platform. The workflow records NEEDS_REVIEW and never retries it.

    Raised by publishers whose message is already safe to store (no token, no caption, no signed URL).
    LinkedIn keeps its own class of the same name (platforms/linkedin/publisher.py).
    """


class ReferenceRecorder(Protocol):
    """Stores a platform's pre-publish reference (Instagram: the container ID) before the publish call.

    It must have committed the reference when it returns, and raise if it could not: a publisher that
    gets an exception from it has not published and must report a definite FAILED (step D wires this
    to the job row; until then it is exercised with fakes).
    """

    def __call__(self, reference: str, /) -> None: ...


class MediaKeyRecorder(Protocol):
    """Stores the media host key of a job's picture before it is uploaded, or clears it (None) once deleted.

    It must have committed when it returns, and raise if it could not. Storing the key first means a picture
    left on the media host by a crash or a NEEDS_REVIEW outcome can always be found and cleaned up.
    """

    def __call__(self, key: str | None, /) -> None: ...


@dataclass(frozen=True)
class RemoteOutcome:
    """What a read-only check of the platform says about a job whose publish outcome was not known."""

    status: JobStatus  # PUBLISHED, FAILED or NEEDS_REVIEW.
    reason: str
    post_id: str | None = None  # Only with PUBLISHED, and only when the platform's post could be identified.

    def __post_init__(self) -> None:
        if self.status not in (JobStatus.PUBLISHED, JobStatus.FAILED, JobStatus.NEEDS_REVIEW):
            raise ValueError("a remote outcome is PUBLISHED, FAILED or NEEDS_REVIEW")
        if self.post_id is not None and self.status is not JobStatus.PUBLISHED:
            raise ValueError("only a PUBLISHED outcome can carry a post ID")
        if not self.reason.strip():
            raise ValueError("a remote outcome needs a reason")


class RemoteChecker(Protocol):
    """Asks the platform, read-only, what became of a job whose publish outcome is unknown. Never publishes."""

    def check(self, reference: str | None, *, job_created_at: datetime) -> RemoteOutcome: ...
