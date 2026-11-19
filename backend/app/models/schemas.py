"""Request bodies. Responses are plain JSON built by ``services/job_service.py``."""

from pydantic import BaseModel

# The text the API requires before a real run or schedule starts (sent by the frontend's confirmation dialog).
# One constant in the core, so the API and the operations can never disagree about it (D49).
from social_media_auto_poster.operations.common import REAL_CONFIRMATION

__all__ = ["REAL_CONFIRMATION", "ReconfirmRequest", "ResolveRequest", "RunRequest", "ScheduleRequest"]


class RunRequest(BaseModel):
    """``POST /api/run``. Simulated unless ``real`` is true; real also needs ``confirm`` to be exactly REAL_CONFIRMATION."""

    real: bool = False
    publish_simulated: bool = False
    confirm: str | None = None
    platform: str | None = None  # Default LinkedIn; a platform without an integration is refused with 400.


class ResolveRequest(BaseModel):
    """``POST /api/jobs/{id}/resolve``: send exactly one of ``published_post_id``, ``failed: true`` or
    ``check: true`` (ask the platform, read-only; Instagram only, and refused while real Instagram is off)."""

    published_post_id: str | None = None
    failed: bool = False
    check: bool = False


class ScheduleRequest(BaseModel):
    """``POST /api/schedules``. ``publish_at`` is ``YYYY-MM-DD HH:MM`` in AUTOPOSTER_TIMEZONE (as typed, never
    converted by the browser). Simulated unless ``real`` is true; real also needs ``confirm`` = REAL_CONFIRMATION."""

    name: str
    publish_at: str
    real: bool = False
    confirm: str | None = None
    platform: str | None = None  # Default LinkedIn; a platform without an integration is refused with 400.


class ReconfirmRequest(BaseModel):
    """``POST /api/schedules/{id}/reconfirm``. Keeps the old time unless ``publish_at`` is given. Real is never
    carried over: it needs ``real`` and ``confirm`` again."""

    publish_at: str | None = None
    real: bool = False
    confirm: str | None = None
