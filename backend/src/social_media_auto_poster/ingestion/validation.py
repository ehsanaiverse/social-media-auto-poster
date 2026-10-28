"""Check that a paired post is acceptable before it is processed.

Every problem is collected, not just the first, so the user can fix them all
at once. A missing file produces only "not found" for that file.
"""

from pathlib import Path

from social_media_auto_poster.models import PostCandidate, ValidationResult

JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Supported extension -> the image type its content must be.
EXPECTED_TYPE = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}

# A safety guard so a wrong or huge file is never read into memory.
# This is NOT LinkedIn's caption limit; that must be verified in Milestone 4.
MAX_CAPTION_BYTES = 64 * 1024


def validate_candidate(candidate: PostCandidate) -> ValidationResult:
    """Validate the media file and the caption file of ``candidate``.

    Only the first few bytes of the image are read (a file-signature check,
    not a full decode); the caption is read up to ``MAX_CAPTION_BYTES``.
    """
    errors = [*_media_errors(candidate.media_path), *_caption_errors(candidate.caption_path)]
    return ValidationResult(is_valid=not errors, errors=tuple(errors))


def _media_errors(path: Path) -> list[str]:
    try:
        with path.open("rb") as f:
            head = f.read(len(PNG_SIGNATURE))
    except FileNotFoundError:
        return ["media file not found"]
    except OSError as error:
        return [f"media file could not be read: {error.strerror or error}"]

    suffix = path.suffix.casefold()
    expected = EXPECTED_TYPE.get(suffix)
    if expected is None:
        allowed = ", ".join(EXPECTED_TYPE)
        return [f"unsupported image type '{path.suffix}' (allowed: {allowed})"]
    if not head:
        return ["image file is empty"]

    actual = _detect_image_type(head)
    if actual is None:
        return [f"image content is not a valid {expected}"]
    if actual != expected:
        return [f"file is {actual} but named {path.suffix}"]
    return []


def _detect_image_type(head: bytes) -> str | None:
    if head.startswith(JPEG_SIGNATURE):
        return "JPEG"
    if head.startswith(PNG_SIGNATURE):
        return "PNG"
    return None


def decode_caption(data: bytes) -> str:
    """The caption text in ``data``: strict UTF-8, with the BOM that Notepad may add removed.

    The single place captions are decoded, so what is validated is what is published.
    Raises ``UnicodeDecodeError`` for anything that is not valid UTF-8.
    """
    return data.decode("utf-8-sig")


def _caption_errors(path: Path) -> list[str]:
    try:
        with path.open("rb") as f:
            data = f.read(MAX_CAPTION_BYTES + 1)
    except FileNotFoundError:
        return ["caption file not found"]
    except OSError as error:
        return [f"caption file could not be read: {error.strerror or error}"]

    if len(data) > MAX_CAPTION_BYTES:
        return [f"caption file too large (over {MAX_CAPTION_BYTES // 1024} KB)"]
    try:
        text = decode_caption(data)
    except UnicodeDecodeError:
        return ["caption is not valid UTF-8 text"]
    if not text.strip():
        return ["caption is empty"]
    return []
