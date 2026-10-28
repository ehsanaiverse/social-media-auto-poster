"""Reading a post's exact content for publishing. Temporary folders only."""

import pytest

from social_media_auto_poster.models import Platform, PostCandidate
from social_media_auto_poster.services.content import ContentNotReadyError, read_post_content
from social_media_auto_poster.services.hashing import sha256_bytes, sha256_file

JPEG_BYTES = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4
CAPTION_TEXT = "Launch day (beta) #product"


@pytest.fixture
def post(tmp_path) -> PostCandidate:
    media, caption = tmp_path / "launch.jpg", tmp_path / "launch.txt"
    media.write_bytes(JPEG_BYTES)
    caption.write_bytes(CAPTION_TEXT.encode("utf-8"))
    return PostCandidate(Platform.LINKEDIN, "launch", media, caption)


def hashes(post: PostCandidate) -> dict[str, str]:
    return {"media_sha256": sha256_file(post.media_path), "caption_sha256": sha256_file(post.caption_path)}


def test_returns_exactly_the_hashed_bytes_and_the_decoded_caption(post):
    recorded = hashes(post)

    content = read_post_content(post, **recorded)

    assert content.media == JPEG_BYTES
    assert content.caption == CAPTION_TEXT
    assert sha256_bytes(content.media) == recorded["media_sha256"]
    assert (content.candidate, content.media_sha256, content.caption_sha256) == (
        post, recorded["media_sha256"], recorded["caption_sha256"],
    )


def test_caption_is_decoded_like_validation_does_it(post):
    post.caption_path.write_bytes(b"\xef\xbb\xbf" + "Grüße".encode("utf-8"))  # Notepad's BOM.

    assert read_post_content(post, **hashes(post)).caption == "Grüße"


@pytest.mark.parametrize(("change", "named"), [
    ("media", "image changed"),
    ("caption", "caption changed"),
    ("both", "image and caption changed"),
])
def test_a_file_changed_after_hashing_is_refused(post, change, named):
    recorded = hashes(post)
    if change in ("media", "both"):
        post.media_path.write_bytes(JPEG_BYTES + b"edited")
    if change in ("caption", "both"):
        post.caption_path.write_text("A different caption", encoding="utf-8")

    with pytest.raises(ContentNotReadyError, match=f"{named} after the duplicate check"):
        read_post_content(post, **recorded)


def test_a_missing_file_is_refused(post):
    recorded = hashes(post)
    post.caption_path.unlink()

    with pytest.raises(ContentNotReadyError, match="could not read the files"):
        read_post_content(post, **recorded)


def test_invalid_utf8_is_refused_even_with_a_matching_hash(post):
    post.caption_path.write_bytes(b"caf\xe9")  # Latin-1, not UTF-8.

    with pytest.raises(ContentNotReadyError, match="not valid UTF-8"):
        read_post_content(post, **hashes(post))


def test_repr_never_shows_the_image_bytes_or_the_caption(post):
    text = repr(read_post_content(post, **hashes(post)))

    assert CAPTION_TEXT not in text and "beta" not in text
    assert "\\xff\\xd8" not in text and "media=" not in text and "caption=" not in text
    assert "launch" in text  # The candidate (paths, stem) is still shown.
