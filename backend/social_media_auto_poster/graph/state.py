"""Graph state (small, serializable) and run context (live dependencies).

State is saved by the checkpointer from Milestone 3, so it holds only plain
values: IDs, path strings, status strings, and lists of error strings. Never
image bytes or file handles. Live objects (publisher, settings) are passed as
LangGraph run context instead, via ``invoke(..., context=...)``.
"""

from dataclasses import dataclass
from typing import TypedDict

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, PostCandidate
from social_media_auto_poster.platforms.base import Publisher


class JobState(TypedDict):
    """The state of one post job as it moves through the graph."""

    job_id: str
    platform: str
    stem: str
    media_path: str
    caption_path: str
    status: str  # A JobStatus value.
    errors: list[str]  # Lists, not tuples: state is serialized as JSON-like data.
    retry_later: bool  # True if a file was still being copied; nothing else happened.
    simulated: bool | None  # Set by the publish node; None if publish never ran.
    platform_post_id: str | None
    destination: str | None  # Folder the files were moved to, if they were moved.


@dataclass(frozen=True)
class WorkflowContext:
    """Dependencies for one graph run. Not part of state; never serialized."""

    publisher: Publisher
    settings: Settings
    stability_interval_s: float


def new_job_state(job_id: str, candidate: PostCandidate) -> JobState:
    """The starting state for a newly discovered candidate."""
    return JobState(
        job_id=job_id,
        platform=candidate.platform.value,
        stem=candidate.stem,
        media_path=str(candidate.media_path),
        caption_path=str(candidate.caption_path),
        status=JobStatus.DISCOVERED.value,
        errors=[],
        retry_later=False,
        simulated=None,
        platform_post_id=None,
        destination=None,
    )
