"""The platform registry: the run path and the schedules look a platform's live parts up here.

A platform's pure post rules (no token, no network) are in ``platforms/rules.py``. This
registry holds the parts that need settings, credentials or a client: building the real
publisher and the token warnings for a real schedule. Only LinkedIn is registered.

The registry never decides whether a run is real. ``real_publisher`` is called only by a run
whose mode is already REAL, and that comes only from ``--real`` on the command line or the
API's typed confirmation phrase (D36, D43).
"""

from datetime import datetime
from typing import Protocol

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations.common import real_forbidden_text
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.platforms.base import Publisher, RemoteChecker
from social_media_auto_poster.platforms.media_host import MediaHost
from social_media_auto_poster.platforms.rules import UnsupportedPlatform

__all__ = ["ADAPTERS", "PlatformAdapter", "UnsupportedPlatform", "adapter_for", "real_unavailable_text"]


class PlatformAdapter(Protocol):
    platform: Platform
    real_available: bool  # Whether a real publisher may be built at all (Instagram: False until D48, D49).
    records_references: bool  # Its publisher records a reference before publishing (Instagram, D1).
    confirmation_phrase: str  # Typed in the API to publish for real to this platform, and only to it (D48).

    def real_publisher(self, settings: Settings) -> Publisher:
        """The platform's real publisher, after its token check. Raises RealPublishingUnavailable (D40)."""
        ...

    def schedule_warnings(self, settings: Settings, publish_at: datetime, now: datetime) -> tuple[str, ...]:
        """Token problems a real schedule for ``publish_at`` will meet; reads backend/.env only, no network."""
        ...

    def remote_checker(self, settings: Settings) -> RemoteChecker | None:
        """The read-only checker for ``resolve --check``, or None if the platform offers no safe check."""
        ...

    def media_host(self, settings: Settings) -> MediaHost | None:
        """Where the platform's publisher stores pictures (Instagram), or None."""
        ...


# Instagram is registered for simulated use only (D49, step D2): its adapter's real_available is False.
ADAPTERS: dict[Platform, PlatformAdapter] = {
    Platform.LINKEDIN: linkedin_ops.ADAPTER,
    Platform.INSTAGRAM: instagram_ops.ADAPTER,
}


def adapter_for(platform: Platform) -> PlatformAdapter:
    try:
        return ADAPTERS[platform]
    except KeyError:
        raise UnsupportedPlatform(f"{platform} is not supported: it has folders but no integration") from None


def confirmation_phrase(platform: Platform) -> str:
    """The phrase the API needs for a real run or schedule on ``platform``; one platform's never works on another."""
    return adapter_for(platform).confirmation_phrase


def real_unavailable_text(platform: Platform) -> str | None:
    """Why real publishing to ``platform`` is refused, or None if its adapter allows it (LinkedIn)."""
    if adapter_for(platform).real_available:
        return None
    return real_forbidden_text(platform)
