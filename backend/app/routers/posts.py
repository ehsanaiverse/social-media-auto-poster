"""``POST /api/posts``: add a post (image + caption) to a platform's incoming folder (LinkedIn by default)."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from social_media_auto_poster.ingestion.intake import MAX_IMAGE_BYTES, IntakeError

from ..dependencies import get_job_service, parse_platform, require_local_marker
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
    platform: str | None = Form(None),
    service: JobService = Depends(get_job_service),
) -> dict:
    """Add a post. It is checked like a file copied in by hand; nothing is published."""
    chosen = parse_platform(platform)  # Before the upload is read or anything is written.
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
            service.add_post, name=name, image=data, image_extension=extension, caption=caption_bytes,
            platform=chosen,
        )
    except IntakeError as error:
        code = INTAKE_STATUS_CODES.get(error.kind, 400)
        raise HTTPException(status_code=code, detail=error.message) from None


@router.post("/check", dependencies=[Depends(require_local_marker)])
async def check_post(
    caption: str = Form(""),
    image: UploadFile = File(...),
    name: str | None = Form(None),
    platform: str | None = Form(None),
    service: JobService = Depends(get_job_service),
) -> dict:
    """Check a post against a platform's rules (the same checks as ``POST /api/posts``). Writes nothing."""
    chosen = parse_platform(platform)
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The image is larger than 10 MB.")
    try:
        caption_bytes = caption.encode("utf-8")  # A lone surrogate (not text) cannot be encoded: 400.
    except UnicodeEncodeError:
        raise HTTPException(status_code=400, detail="The caption is not valid text.") from None
    return service.check_post(name=name, image=data, image_extension=_extension(image.filename or ""),
                              caption=caption_bytes, platform=chosen)


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""
