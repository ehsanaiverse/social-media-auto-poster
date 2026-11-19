"""Each platform's own post rules, as plain functions: no token, no network, no HTTP client.

Validation (``ingestion/validation.py``) and scheduling (``services/scheduling.py``) look a
platform up here instead of importing one platform's checks. The shared checks (image type,
file signature, UTF-8, empty caption, size caps) stay in validation; only what differs per
platform is here. A platform without an entry gets the shared checks only; ``rules_for`` is for
callers that must never skip a platform's checks, and refuses such a platform instead.
"""

from collections.abc import Callable
from dataclasses import dataclass

from social_media_auto_poster.models import Platform
from social_media_auto_poster.platforms.instagram import rules as instagram
from social_media_auto_poster.platforms.linkedin.caption import caption_problem as linkedin_caption_problem
from social_media_auto_poster.platforms.linkedin.image_size import image_problem as linkedin_image_problem


@dataclass(frozen=True)
class PostRules:
    """What one platform accepts. Each check returns every reason a post cannot be published (empty: fine)."""

    platform: Platform
    display_name: str  # How messages name the platform ("LinkedIn").
    # Checked in every mode, so scan, every run and uploads refuse the caption (D45).
    caption_problems: Callable[[str], list[str]]
    # Checked on the real path only (a real schedule; the real publisher checks it again itself).
    real_media_problems: Callable[[bytes], list[str]]
    # Checked in every mode, after the shared image checks pass, on the file read up to media_read_limit + 1
    # bytes. None (LinkedIn): validation reads only the image's first 8 bytes, as before.
    media_problems: Callable[[bytes], list[str]] | None = None
    media_read_limit: int | None = None

    def __post_init__(self) -> None:
        # Validation reads media_read_limit + 1 bytes for media_problems: one without the other is a mistake.
        if (self.media_problems is None) != (self.media_read_limit is None):
            raise ValueError("media_problems and media_read_limit must be given together")
        if self.media_read_limit is not None and self.media_read_limit < 1:
            raise ValueError("media_read_limit must be at least 1")


class UnsupportedPlatform(LookupError):
    """The platform has folders but no integration (no adapter, or no post rules)."""


def _as_list(check: Callable[[object], str | None]) -> Callable[[object], list[str]]:
    def problems(value: object) -> list[str]:
        problem = check(value)
        return [problem] if problem is not None else []

    return problems


RULES: dict[Platform, PostRules] = {
    Platform.LINKEDIN: PostRules(
        platform=Platform.LINKEDIN,
        display_name="LinkedIn",
        caption_problems=_as_list(linkedin_caption_problem),
        real_media_problems=_as_list(linkedin_image_problem),
    ),
    # Rules only: Instagram has no adapter, so no run, scan, schedule or API call can reach it yet (D49).
    Platform.INSTAGRAM: PostRules(
        platform=Platform.INSTAGRAM,
        display_name="Instagram",
        caption_problems=instagram.caption_problems,
        real_media_problems=lambda data: [],  # Every Instagram picture rule applies in every mode.
        media_problems=instagram.media_problems,
        media_read_limit=instagram.MAX_IMAGE_BYTES,
    ),
}


def rules_for(platform: Platform) -> PostRules:
    """The platform's rules, for a caller that must not skip them. Raises UnsupportedPlatform if it has none."""
    try:
        return RULES[platform]
    except KeyError:
        raise UnsupportedPlatform(f"{platform} has no post rules") from None


def display_name(platform: Platform) -> str:
    """How messages name ``platform``: its rules' display name, or its folder name if it has no rules."""
    rules = RULES.get(platform)
    return rules.display_name if rules is not None else platform.value
