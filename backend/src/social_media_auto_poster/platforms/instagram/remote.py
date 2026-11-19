"""Settle an Instagram job whose publish outcome is unknown, by reading the container's status (read-only).

Instagram documents the container status (S16), so, unlike LinkedIn, an uncertain publish can be
checked safely (CLAUDE.md: "unless the platform offers a safe way to check"). This never publishes
and never retries anything.

  no recorded container ID     -> FAILED: media_publish is only called after the ID is recorded
  PUBLISHED                    -> PUBLISHED (the media ID too, if exactly one new picture is found, S13/S17)
  ERROR or EXPIRED             -> FAILED: the container can no longer be published (S16: expires after 24 h)
  FINISHED, IN_PROGRESS, or a status that cannot be read -> NEEDS_REVIEW
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from social_media_auto_poster.models import JobStatus
from social_media_auto_poster.platforms.base import RemoteOutcome
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.publisher import json_or_empty

CONTAINER_LIFETIME = timedelta(hours=24)  # S13: "Containers expire after 24 hours".
MATCH_MARGIN = timedelta(minutes=5)  # Clock differences between this PC and Instagram.


class InstagramRemoteChecker:
    def __init__(self, client: InstagramClient, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._client = client
        self._now = now

    def __repr__(self) -> str:
        return "InstagramRemoteChecker()"

    def check(self, container_id: str | None, *, job_created_at: datetime) -> RemoteOutcome:
        """What Instagram says about the job started at ``job_created_at`` with this recorded container ID."""
        if not container_id:
            return RemoteOutcome(
                JobStatus.FAILED,
                "no container ID was recorded, so media_publish was never called; nothing was published",
            )
        status = self._status(container_id)
        if status == "PUBLISHED":
            media_id = self._find_media(job_created_at)
            note = f" as media {media_id}" if media_id else " (its media ID could not be identified)"
            return RemoteOutcome(JobStatus.PUBLISHED, f"Instagram reports container {container_id} as published{note}",
                                 post_id=media_id)
        if status in ("ERROR", "EXPIRED"):
            return RemoteOutcome(
                JobStatus.FAILED, f"Instagram reports container {container_id} as {status}; it was never published"
            )
        age = self._now() - job_created_at
        state = f"reports container {container_id} as {status}" if status else "could not be read"
        if age < CONTAINER_LIFETIME:
            hours = (CONTAINER_LIFETIME - age).total_seconds() / 3600
            when = f"check again in about {hours:.0f} hour(s), once an unpublished container has expired"
        else:
            when = "the container is older than 24 hours but not EXPIRED; check Instagram by hand"
        return RemoteOutcome(JobStatus.NEEDS_REVIEW, f"Instagram {state}; {when}")

    def _status(self, container_id: str) -> str | None:
        try:
            response = self._client.container_status(container_id)
        except (httpx.HTTPError, ValueError):
            return None
        if response.status_code != 200:
            return None
        status = json_or_empty(response).get("status_code")
        return status if status in ("PUBLISHED", "ERROR", "EXPIRED", "FINISHED", "IN_PROGRESS") else None

    def _find_media(self, job_created_at: datetime) -> str | None:
        """The ID of the one picture published since the job started, or None if there is not exactly one."""
        since = job_created_at - MATCH_MARGIN
        try:
            response = self._client.recent_media(int(since.timestamp()))
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        items = json_or_empty(response).get("data")
        if not isinstance(items, list):
            return None
        found = [item.get("id") for item in items
                 if isinstance(item, dict) and item.get("media_type") == "IMAGE" and _after(item.get("timestamp"), since)]
        return found[0] if len(found) == 1 and isinstance(found[0], str) and found[0].isdigit() else None


def _after(timestamp: object, since: datetime) -> bool:
    """Whether an IG Media ``timestamp`` (ISO 8601 UTC, S17) is at or after ``since``."""
    if not isinstance(timestamp, str):
        return False
    try:
        moment = datetime.fromisoformat(timestamp.replace("+0000", "+00:00"))
    except ValueError:
        return False
    return moment.tzinfo is not None and moment >= since
