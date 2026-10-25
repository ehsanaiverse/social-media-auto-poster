"""Shared data types used by ingestion, the workflow graph, and publishers."""

from dataclasses import dataclass
from enum import StrEnum


class Platform(StrEnum):
    """Platforms with content folders. Values match the folder names under ``data/``.

    Only LinkedIn is processed; Facebook and Instagram exist so their folders
    can be created, not because they are integrated.
    """

    LINKEDIN = "LinkedIn"
    FACEBOOK = "Facebook"
    INSTAGRAM = "Instagram"


class JobStatus(StrEnum):
    """Lifecycle of one post job. Values are plain strings, safe for graph state and SQLite."""

    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"  # A real platform confirmed the post.
    SIMULATED = "SIMULATED"  # Terminal. A simulated run never ends in PUBLISHED.
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"  # Outcome unknown; must not be retried automatically.


@dataclass(frozen=True)
class PublishResult:
    """A publish attempt that returned a definite answer.

    Ambiguous outcomes (timeouts, lost responses) are not represented here;
    they lead to ``NEEDS_REVIEW`` instead. A real result always carries the
    platform's post ID that confirms it, and a simulated result never does.
    """

    simulated: bool
    message: str
    platform_post_id: str | None = None

    def __post_init__(self) -> None:
        if self.simulated and self.platform_post_id is not None:
            raise ValueError("A simulated result must not carry a platform post ID.")
        if not self.simulated and not self.platform_post_id:
            raise ValueError("A real publish result requires the platform post ID that confirms it.")

    @property
    def status(self) -> JobStatus:
        """The terminal job status this result leads to."""
        return JobStatus.SIMULATED if self.simulated else JobStatus.PUBLISHED
