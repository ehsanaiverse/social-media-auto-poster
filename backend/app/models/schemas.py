"""Request bodies. Responses are plain JSON built by ``services/job_service.py``."""

from pydantic import BaseModel

# The text the API requires before a real run starts. Sent by the frontend's confirmation dialog.
REAL_CONFIRMATION = "publish to LinkedIn"


class RunRequest(BaseModel):
    """``POST /api/run``. Simulated unless ``real`` is true; real also needs ``confirm`` to be exactly REAL_CONFIRMATION."""

    real: bool = False
    publish_simulated: bool = False
    confirm: str | None = None


class ResolveRequest(BaseModel):
    """``POST /api/jobs/{id}/resolve``: send exactly one of ``published_post_id`` or ``failed: true``."""

    published_post_id: str | None = None
    failed: bool = False
