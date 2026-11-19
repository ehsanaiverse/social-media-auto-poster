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
from social_media_auto_poster.operations import linkedin as linkedin_ops
from social_media_auto_poster.platforms.base import Publisher
from social_media_auto_poster.platforms.rules import UnsupportedPlatform

__all__ = ["ADAPTERS", "PlatformAdapter", "UnsupportedPlatform", "adapter_for"]


class PlatformAdapter(Protocol):
    platform: Platform
    real_available: bool  # Whether a real publisher may be built at all (Instagram: False until D48, D49).

    def real_publisher(self, settings: Settings) -> Publisher:
        """The platform's real publisher, after its token check. Raises RealPublishingUnavailable (D40)."""
        ...

    def schedule_warnings(self, settings: Settings, publish_at: datetime, now: datetime) -> tuple[str, ...]:
        """Token problems a real schedule for ``publish_at`` will meet; reads backend/.env only, no network."""
        ...


ADAPTERS: dict[Platform, PlatformAdapter] = {Platform.LINKEDIN: linkedin_ops.ADAPTER}


def adapter_for(platform: Platform) -> PlatformAdapter:
    try:
        return ADAPTERS[platform]
    except KeyError:
        raise UnsupportedPlatform(f"{platform} is not supported: it has folders but no integration") from None
