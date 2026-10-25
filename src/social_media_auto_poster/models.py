"""Shared data types used by ingestion, the workflow graph, and publishers."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


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
class PostCandidate:
    """One paired post found by ingestion: an image and a caption sharing a filename stem.

    Paths stay ``Path`` objects here; the graph converts them to strings when
    it builds its serializable state.
    """

    platform: Platform
    stem: str
    media_path: Path
    caption_path: Path


@dataclass(frozen=True)
class ScanResult:
    """The outcome of scanning one ``incoming/`` folder.

    ``pending`` holds unpaired files. They are not errors and are left
    untouched until their partner arrives. ``ambiguous`` holds one group of
    files per stem that cannot be paired unambiguously (for example two
    images and one caption); they are also left untouched.
    """

    candidates: tuple[PostCandidate, ...]
    pending: tuple[Path, ...]
    ambiguous: tuple[tuple[Path, ...], ...] = ()

    def __post_init__(self) -> None:
        _require_tuple("candidates", self.candidates)
        _require_tuple("pending", self.pending)
        _require_tuple("ambiguous", self.ambiguous)
        for group in self.ambiguous:
            _require_tuple("each ambiguous group", group)


@dataclass(frozen=True)
class ValidationResult:
    """Whether a candidate passed validation, and every reason it did not."""

    is_valid: bool
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_tuple("errors", self.errors)
        if self.is_valid and self.errors:
            raise ValueError("A valid result must not carry errors.")
        if not self.is_valid and not self.errors:
            raise ValueError("An invalid result requires at least one error.")


@dataclass(frozen=True)
class PublishResult:
    """A publish attempt that returned a definite answer: success or failure.

    Ambiguous outcomes (timeouts, lost responses, a crash while publishing)
    are never represented here; they lead to ``NEEDS_REVIEW`` instead. A real
    success always carries the platform's post ID that confirms it, and a
    simulated result never does.
    """

    success: bool
    simulated: bool
    platform_post_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.simulated and self.platform_post_id is not None:
            raise ValueError("A simulated result must not carry a platform post ID.")
        if self.success and self.error is not None:
            raise ValueError("A successful result must not carry an error.")
        if not self.success and not (self.error and self.error.strip()):
            raise ValueError("A failed result requires a non-empty error.")
        has_post_id = bool(self.platform_post_id and self.platform_post_id.strip())
        if self.success and not self.simulated and not has_post_id:
            raise ValueError("A real publish result requires the platform post ID that confirms it.")

    @property
    def status(self) -> JobStatus:
        """The terminal job status this result leads to."""
        if not self.success:
            return JobStatus.FAILED
        return JobStatus.SIMULATED if self.simulated else JobStatus.PUBLISHED


def _require_tuple(name: str, value: object) -> None:
    # Frozen dataclasses only block reassignment; a list inside would still be mutable.
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple, not {type(value).__name__}.")
