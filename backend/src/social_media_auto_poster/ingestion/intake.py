"""Adding a post to a platform's incoming folder from outside the folder (the local API upload).

The upload goes through the same checks as a file the user copies in by hand:
``validate_candidate`` (extension, file signature, UTF-8 caption) decides whether
it is accepted. Nothing is written under the post's final name until it passes,
and an existing post is never overwritten.
"""

import os
import uuid
from pathlib import Path

from social_media_auto_poster.config import Settings
from social_media_auto_poster.ingestion.validation import EXPECTED_TYPE, MAX_CAPTION_BYTES, validate_candidate
from social_media_auto_poster.models import Platform, PostCandidate

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # Proposal from docs/api-plan.md (open question 3).
FORBIDDEN_STEM_CHARS = frozenset('<>:"/\\|?*')
HIDDEN_PREFIXES = (".", "~$")  # The scanner ignores these names, so a name starting with them could never post.


class IntakeError(Exception):
    """A post could not be added. ``kind`` says why, in words the caller can map to a status code."""

    def __init__(self, kind: str, message: str, details: tuple[str, ...] = ()):
        super().__init__(message)
        self.kind = kind  # no_folder | invalid_name | unsupported_type | too_large | invalid_post | name_taken
        self.message = message
        self.details = details


def check_post_name(name: str) -> str:
    """Return the cleaned post name, or raise ``ValueError`` if it cannot be a post name."""
    stem = name.strip()
    if not stem:
        raise ValueError("The name must not be empty.")
    if FORBIDDEN_STEM_CHARS & set(stem):
        raise ValueError(f"The name must not contain any of {''.join(sorted(FORBIDDEN_STEM_CHARS))}.")
    if stem.startswith(HIDDEN_PREFIXES):
        raise ValueError("Names starting with '.' or '~$' are ignored by the scanner.")
    return stem


def add_post(
    settings: Settings,
    *,
    name: str,
    image: bytes,
    image_extension: str,
    caption: bytes,
    platform: Platform = Platform.LINKEDIN,
) -> PostCandidate:
    """Write a new post (image + caption) into the platform's incoming folder and return it.

    Raises ``IntakeError``; on any error nothing is left behind in incoming/.
    """
    incoming = settings.incoming_dir(platform)
    if not incoming.is_dir():
        raise IntakeError("no_folder", f"the incoming folder does not exist: {incoming}")
    try:
        stem = check_post_name(name)
    except ValueError as error:
        raise IntakeError("invalid_name", str(error)) from None

    extension = image_extension.lower()
    if extension not in EXPECTED_TYPE:
        raise IntakeError("unsupported_type", "the image must be a .jpg, .jpeg or .png file")
    if len(image) > MAX_IMAGE_BYTES:
        raise IntakeError("too_large", f"the image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    if len(caption) > MAX_CAPTION_BYTES:
        raise IntakeError("too_large", f"the caption is larger than {MAX_CAPTION_BYTES // 1024} KB")
    if _stem_taken(incoming, stem):
        raise IntakeError("name_taken", f"a post named '{stem}' is already in incoming/")

    # Write under hidden temporary names (the scanner ignores them), check them, then give them the final names.
    token = uuid.uuid4().hex
    temp_media = incoming / f".upload-{token}{extension}"
    temp_caption = incoming / f".upload-{token}.txt"
    final_media = incoming / f"{stem}{extension}"
    final_caption = incoming / f"{stem}.txt"
    try:
        _write_new(temp_media, image)
        _write_new(temp_caption, caption)
        check = PostCandidate(platform=platform, stem=stem, media_path=temp_media, caption_path=temp_caption)
        result = validate_candidate(check)
        if not result.is_valid:
            details = tuple(_name_the_final_file(error, temp_media, temp_caption, final_media, final_caption)
                            for error in result.errors)
            raise IntakeError("invalid_post", "the post was not accepted: " + "; ".join(details), details)
        _publish_new(temp_media, final_media)
        try:
            _publish_new(temp_caption, final_caption)
        except IntakeError:
            final_media.unlink(missing_ok=True)  # Both files or neither.
            raise
    finally:
        temp_media.unlink(missing_ok=True)
        temp_caption.unlink(missing_ok=True)
    return PostCandidate(platform=platform, stem=stem, media_path=final_media, caption_path=final_caption)


def _stem_taken(folder: Path, stem: str) -> bool:
    wanted = stem.lower()
    return any(path.is_file() and path.stem.lower() == wanted and not path.name.startswith(HIDDEN_PREFIXES)
               for path in folder.iterdir())


def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as handle:  # "x": never overwrites.
        handle.write(data)


def _publish_new(temp: Path, final: Path) -> None:
    """Give ``temp`` the final name without ever replacing an existing file."""
    try:
        os.link(temp, final)
    except FileExistsError:
        raise IntakeError("name_taken", f"{final.name} already exists in incoming/") from None


def _name_the_final_file(error: str, temp_media: Path, temp_caption: Path, final_media: Path, final_caption: Path) -> str:
    """Validation messages name the temporary files; show the names the post will have instead."""
    return error.replace(temp_media.name, final_media.name).replace(temp_caption.name, final_caption.name)
