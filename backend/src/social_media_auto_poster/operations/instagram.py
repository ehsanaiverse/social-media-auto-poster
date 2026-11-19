"""Instagram's adapter (step C). NOT registered in operations/platforms.py: Instagram stays unreachable until step D.

Real Instagram is forbidden until D48 is accepted (D49), so ``real_available`` is False and building the
real publisher is refused, even when called directly, before any setting or token is read. A second
guard sits in ``platforms/instagram/client.py``: the client refuses any transport except a test one.
"""

import os
from datetime import datetime

import httpx
from dotenv import dotenv_values

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations.common import RealPublishingUnavailable
from social_media_auto_poster.platforms.base import Publisher
from social_media_auto_poster.platforms.instagram.client import InstagramClient, RealInstagramForbidden

TOKEN_VAR = "INSTAGRAM_ACCESS_TOKEN"
USER_ID_VAR = "INSTAGRAM_USER_ID"
NOT_AVAILABLE = (
    "real Instagram publishing is not available: it is forbidden until D48 is accepted (D49); nothing was published"
)


class InstagramAdapter:
    """Instagram for the platform registry. Simulated only (D49): it never builds a real publisher."""

    platform = Platform.INSTAGRAM
    real_available = False

    def real_publisher(self, settings: Settings) -> Publisher:
        return _real_publisher(settings)

    def schedule_warnings(self, settings: Settings, publish_at: datetime, now: datetime) -> tuple[str, ...]:
        return ()  # No real Instagram schedule can exist yet (D49).


ADAPTER = InstagramAdapter()


def _real_publisher(settings: Settings) -> Publisher:
    """Refused while ``real_available`` is False, before any setting or token is read."""
    if not InstagramAdapter.real_available:
        raise RealPublishingUnavailable(NOT_AVAILABLE)
    values = {**dotenv_values(settings.base_dir / ".env"), **os.environ}  # Real environment variables win.
    token, user_id = (values.get(TOKEN_VAR) or "").strip(), (values.get(USER_ID_VAR) or "").strip()
    if not token or not user_id:
        raise RealPublishingUnavailable(f"{TOKEN_VAR} and {USER_ID_VAR} must be set in backend\\.env")
    try:
        InstagramClient(token, user_id=user_id, transport=httpx.HTTPTransport(retries=0))
    except (RealInstagramForbidden, ValueError) as error:
        raise RealPublishingUnavailable(str(error)) from None
    raise RealPublishingUnavailable(NOT_AVAILABLE)  # No media host exists yet (future, D48).
