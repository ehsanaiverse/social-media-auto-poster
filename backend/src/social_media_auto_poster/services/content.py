"""Read a post's exact content for publishing.

The duplicate check hashes both files and records the hashes; publishing happens
a little later. If a file were replaced in between, the new content would be
posted under the old hashes. So the publish step reads each file exactly once,
here, checks it against the recorded hash, and hands those bytes to the publisher.
Any problem raises before anything is sent, which makes it a definite FAILED.
"""

from social_media_auto_poster.ingestion.validation import decode_caption
from social_media_auto_poster.models import PostCandidate, PostContent
from social_media_auto_poster.services.hashing import sha256_bytes


class ContentNotReadyError(Exception):
    """The files cannot be published as checked: changed, unreadable, or not valid UTF-8. Nothing was sent."""


def read_post_content(candidate: PostCandidate, *, media_sha256: str, caption_sha256: str) -> PostContent:
    """Read both files once and return them, if they still match the recorded hashes.

    Videos (.mp4, .mov) are never read into memory here: their bytes are streamed
    in chunks during upload and verified against media_sha256 on the wire (D51).
    """
    is_video = candidate.media_path.suffix.casefold() in (".mp4", ".mov")
    try:
        if not candidate.media_path.is_file():
            raise FileNotFoundError(f"media file not found: {candidate.media_path}")
        media = b"" if is_video else candidate.media_path.read_bytes()
        caption_bytes = candidate.caption_path.read_bytes()
    except OSError as error:
        raise ContentNotReadyError(f"could not read the files to publish them ({error})") from error

    if not is_video and sha256_bytes(media) != media_sha256:
        changed = ["image"]
        if sha256_bytes(caption_bytes) != caption_sha256:
            changed.append("caption")
        raise ContentNotReadyError(f"{' and '.join(changed)} changed after the duplicate check")
    elif sha256_bytes(caption_bytes) != caption_sha256:
        raise ContentNotReadyError("caption changed after the duplicate check")

    try:
        caption = decode_caption(caption_bytes)
    except UnicodeDecodeError as error:
        raise ContentNotReadyError("caption is not valid UTF-8 text") from error

    return PostContent(
        candidate=candidate,
        media_sha256=media_sha256,
        caption_sha256=caption_sha256,
        media=media,
        caption=caption,
    )
