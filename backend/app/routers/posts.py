"""``POST /api/posts``: add a post (image + caption) to the LinkedIn incoming folder."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from social_media_auto_poster.ingestion.intake import MAX_IMAGE_BYTES, IntakeError

from ..dependencies import get_job_service, require_local_marker
from ..services import JobService

router = APIRouter(prefix="/api/posts", tags=["posts"])

INTAKE_STATUS_CODES = {
    "no_folder": 404,
    "invalid_name": 400,
    "unsupported_type": 400,
    "invalid_post": 400,
    "too_large": 413,
    "name_taken": 409,
}


@router.post("", status_code=201, dependencies=[Depends(require_local_marker)])
async def create_post(
    name: str = Form(...),
    caption: str = Form(""),
    image: UploadFile = File(...),
    service: JobService = Depends(get_job_service),
) -> dict:
    """Add a post. It is checked like a file copied in by hand; nothing is published."""
    # Read one byte past the limit: a larger upload is refused without being kept in memory.
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The image is larger than 10 MB.")
    try:
        caption_bytes = caption.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(status_code=400, detail="The caption is not valid text.") from None
    extension = _extension(image.filename or "")
    try:
        return await run_in_threadpool(
            service.add_post, name=name, image=data, image_extension=extension, caption=caption_bytes
        )
    except IntakeError as error:
        code = INTAKE_STATUS_CODES.get(error.kind, 400)
        raise HTTPException(status_code=code, detail=error.message) from None


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""
