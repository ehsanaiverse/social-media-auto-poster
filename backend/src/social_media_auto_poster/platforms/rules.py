"""Each platform's own post rules, as plain functions: no token, no network, no HTTP client.

Validation (``ingestion/validation.py``) and scheduling (``services/scheduling.py``) look a
platform up here instead of importing one platform's checks. The shared checks (image type,
file signature, UTF-8, empty caption, size caps) stay in validation; only what differs per
platform is here. A platform without an entry gets the shared checks only.
"""

from collections.abc import Callable
from dataclasses import dataclass

from social_media_auto_poster.models import Platform
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
}
