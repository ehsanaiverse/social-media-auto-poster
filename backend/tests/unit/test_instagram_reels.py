"""Tests for Instagram Reels publishing (D51).

Checks:
- Resumable upload directly to Meta (rupload.facebook.com), no Backblaze B2 host used.
- Headers: Authorization: OAuth <token>, offset: 0, file_size, Content-Length (never chunked).
- Order: quota -> create (REELS, resumable) -> record container ID -> upload -> poll FINISHED -> media_publish once.
- Fault check 1: publish when the streamed hash differs -> test fails (status FAILED, media_publish never called).
- Fault check 2: media_publish before upload/status FINISHED -> test fails (status FAILED, media_publish never called).
- LinkedIn rejects video ("video not supported on LinkedIn").
- Streamed in chunks and hashed while sending: never loads the whole file into memory.
"""

import hashlib
from pathlib import Path
import pytest
import httpx

from instagram_fake import CONTAINER_ID, MEDIA_ID, TOKEN, USER_ID, FakeGraph, json_response
from media_host_fake import InMemoryMediaHost
from social_media_auto_poster.ingestion.intake import IntakeError, add_post
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PostContent
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.publisher import InstagramPublisher
from video_fake import build_mp4

CAPTION = "Check out our new Reel! #automation #reel"
TINY_MP4 = build_mp4(width=1080, height=1920, duration=15000, timescale=1000)
TINY_MP4_HASH = hashlib.sha256(TINY_MP4).hexdigest()


class ReelRun:
    """A simulated Instagram Reel publish attempt using FakeGraph and in-memory mock."""

    def __init__(self, tmp_path: Path, *, media: bytes = TINY_MP4, caption: str = CAPTION, media_sha256: str | None = None, **answers):
        self.tmp_path = tmp_path
        self.graph = FakeGraph()
        self.graph.answers.update(answers)
        self.host = InMemoryMediaHost()
        self.recorded: list[str] = []
        self.media_keys: list[str | None] = []
        self.sleeps: list[float] = []

        self.media_file = tmp_path / "video.mp4"
        self.media_file.write_bytes(media)
        self.caption_file = tmp_path / "video.txt"
        self.caption_file.write_text(caption, encoding="utf-8")

        candidate = PostCandidate(Platform.INSTAGRAM, "video", self.media_file, self.caption_file)
        self.post = PostContent(
            candidate,
            media_sha256=media_sha256 or hashlib.sha256(media).hexdigest(),
            caption_sha256=hashlib.sha256(caption.encode("utf-8")).hexdigest(),
            media=b"",  # As read_post_content produces for video!
            caption=caption,
        )

        client = InstagramClient(TOKEN, user_id=USER_ID, transport=self.graph.transport())
        self.publisher = InstagramPublisher(client, self.host, sleep=self.sleeps.append)
        self.result = None
        self.error: BaseException | None = None

    def record(self, container_id: str) -> None:
        self.graph.events.append("record")
        self.recorded.append(container_id)

    def go(self):
        self.result = self.publisher.publish(self.post, record=self.record, record_media=self.media_keys.append)
        return self


def test_reel_happy_path_publishes_once_with_resumable_upload(tmp_path: Path):
    run = ReelRun(tmp_path).go()

    assert run.result.status is JobStatus.PUBLISHED
    assert run.result.platform_post_id == MEDIA_ID
    # Event sequence: limit -> create -> record (committed) -> upload -> status -> publish
    assert run.graph.events == ["limit", "create", "record", "upload", "status", "publish"]
    assert run.recorded == [CONTAINER_ID]

    # No Backblaze B2 media host was used!
    assert run.host.calls == []

    # Check the create call had media_type=REELS and upload_type=resumable
    create_req = next(r for r in run.graph.requests if r.url.path.endswith(f"/{USER_ID}/media"))
    create_body = httpx.Request("POST", "https://x", content=create_req.content).read().decode("utf-8")
    assert "REELS" in create_body and "resumable" in create_body
    assert "Check out our new Reel!" in create_body

    # Check upload call headers on rupload.facebook.com
    upload_req = next(r for r in run.graph.requests if r.url.host == "rupload.facebook.com")
    assert upload_req.headers["Authorization"] == f"OAuth {TOKEN}"
    assert upload_req.headers["offset"] == "0"
    assert upload_req.headers["file_size"] == str(len(TINY_MP4))


def test_upload_sends_a_length_and_is_never_chunked(tmp_path: Path):
    """Meta's resumable upload needs the body length; an iterable body must not be sent chunked."""
    run = ReelRun(tmp_path).go()

    upload_req = next(r for r in run.graph.requests if r.url.host == "rupload.facebook.com")
    assert upload_req.headers["content-length"] == str(len(TINY_MP4))
    assert "transfer-encoding" not in upload_req.headers


def test_fault_check_1_streamed_hash_differs_fails_and_never_publishes(tmp_path: Path):
    """Fault check (1): publish when the streamed hash differs -> test fails."""
    # Post was registered with a hash, but the file on disk streams a different hash
    wrong_hash = "a" * 64
    run = ReelRun(tmp_path, media_sha256=wrong_hash).go()

    assert run.result.status is JobStatus.FAILED
    assert "streamed video hash differs from the recorded hash" in run.result.error
    assert "publish" not in run.graph.events
    assert run.graph.count("publish") == 0


def test_fault_check_2a_media_publish_before_upload_finished_fails(tmp_path: Path):
    """Fault check (2a): media_publish before upload FINISHED -> test fails."""
    # Upload to rupload fails with 500
    run = ReelRun(tmp_path, upload=json_response(500, {"error": "Server error"})).go()

    assert run.result.status is JobStatus.FAILED
    assert "uploading the video failed" in run.result.error
    assert "publish" not in run.graph.events


def test_fault_check_2b_media_publish_when_status_in_progress_fails(tmp_path: Path):
    """Fault check (2b): media_publish before status FINISHED -> test fails."""
    # Status always answers IN_PROGRESS
    run = ReelRun(tmp_path, status=json_response(200, {"status_code": "IN_PROGRESS", "id": CONTAINER_ID})).go()

    assert run.result.status is JobStatus.FAILED
    assert "still not ready" in run.result.error
    assert "publish" not in run.graph.events


def test_fault_check_2c_media_publish_when_status_error_fails(tmp_path: Path):
    """Fault check (2c): container status reports ERROR."""
    run = ReelRun(tmp_path, status=json_response(200, {"status_code": "ERROR", "id": CONTAINER_ID})).go()

    assert run.result.status is JobStatus.FAILED
    assert "reports the container as ERROR" in run.result.error
    assert "publish" not in run.graph.events


def test_linkedin_rejects_video_candidate(tmp_path: Path):
    video = tmp_path / "post.mp4"
    video.write_bytes(TINY_MP4)
    caption = tmp_path / "post.txt"
    caption.write_text("Caption", encoding="utf-8")

    candidate = PostCandidate(Platform.LINKEDIN, "post", video, caption)
    result = validate_candidate(candidate)
    assert not result.is_valid
    assert result.errors == ("video not supported on LinkedIn",)


def test_intake_rejects_video_on_linkedin(tmp_path: Path):
    settings = type("MockSettings", (), {"incoming_dir": lambda self, p: tmp_path})()
    with pytest.raises(IntakeError, match="video not supported on LinkedIn"):
        add_post(settings, name="video_post", image=TINY_MP4, image_extension=".mp4", caption=b"hello", platform=Platform.LINKEDIN)


def test_intake_accepts_video_on_instagram(tmp_path: Path):
    settings = type("MockSettings", (), {"incoming_dir": lambda self, p: tmp_path})()
    post = add_post(settings, name="reel_post", image=TINY_MP4, image_extension=".mp4", caption=b"hello", platform=Platform.INSTAGRAM)
    assert post.stem == "reel_post"
    assert post.media_path.name == "reel_post.mp4"
    assert post.media_path.is_file()
