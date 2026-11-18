"""``linkedin-check``: is real posting set up correctly? Never creates a post.

1. The token: present, and how long until it expires.
2. The member: ``GET /v2/userinfo`` with the token (read-only).
3. With ``--upload-test`` only: register and upload a small generated image, then
   read its processing status. This answers whether this token may read image
   status, which the publisher relies on before every post. The image is never
   used in a post, so no one can see it.
"""

import struct
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import httpx

from social_media_auto_poster.platforms.linkedin.client import LINKEDIN_VERSION, LinkedInClient
from social_media_auto_poster.platforms.linkedin.credentials import LinkedInCredentials, TokenState

UPLOAD_TEST_SIZE = 200  # Pixels per side of the generated test image.
POLL_ATTEMPTS = 15
POLL_INTERVAL_S = 2.0


@dataclass
class CheckReport:
    lines: list[str] = field(default_factory=list)
    ok: bool = True
    # Structured results for the API (the CLI prints ``lines`` only).
    member: tuple[str, str] | None = None  # (display name, author URN) once LinkedIn accepts the token.
    unreachable: bool = False  # LinkedIn could not be reached at all (network error).
    problems: list[str] = field(default_factory=list)  # The PROBLEM texts, without the "PROBLEM" label.

    def good(self, text: str) -> None:
        self.lines.append(f"  OK       {text}")

    def warn(self, text: str) -> None:
        self.lines.append(f"  WARNING  {text}")

    def bad(self, text: str) -> None:
        self.lines.append(f"  PROBLEM  {text}")
        self.problems.append(text)
        self.ok = False


def run_check(
    credentials: LinkedInCredentials,
    client: LinkedInClient,
    *,
    today: date,
    upload_test: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> CheckReport:
    report = CheckReport()
    report.good(f"LinkedIn API version {LINKEDIN_VERSION}")

    state = credentials.state(today)
    expiry = credentials.describe(today)
    if state is TokenState.EXPIRED:
        report.bad(expiry)
        return report  # LinkedIn would reject it anyway; nothing is sent.
    (report.good if state is TokenState.VALID else report.warn)(f"access token is set; {expiry}")

    author = _check_member(report, client)
    if author is None:
        return report
    if upload_test:
        _check_image_upload(report, client, author, sleep)
    else:
        report.lines.append("  -        image status not checked (add --upload-test; it uploads a test image, no post)")
    return report


def _check_member(report: CheckReport, client: LinkedInClient) -> str | None:
    try:
        response = client.userinfo()
    except httpx.HTTPError as error:
        report.unreachable = True
        report.bad(f"could not reach LinkedIn ({type(error).__name__})")
        return None
    if response.status_code == 401:
        report.bad("LinkedIn rejected the access token (401): it has expired or is invalid")
        return None
    if not response.is_success:
        report.bad(f"LinkedIn answered {response.status_code} to /v2/userinfo")
        return None
    data = response.json()
    member_id = data.get("sub") if isinstance(data, dict) else None
    if not member_id:
        report.bad("LinkedIn did not return a member ID")
        return None
    author = f"urn:li:person:{member_id}"
    name = data.get("name") or "this member"
    report.member = (name, author)
    report.good(f"token accepted; posts would be published as {name} ({author})")
    return author


def _check_image_upload(report: CheckReport, client: LinkedInClient, author: str, sleep) -> None:
    try:
        registered = client.initialize_image_upload(author)
        if not registered.is_success:
            report.bad(f"registering a test image upload: LinkedIn answered {registered.status_code}")
            return
        value = registered.json().get("value") or {}
        image_urn, upload_url = value.get("image"), value.get("uploadUrl")
        if not (isinstance(image_urn, str) and isinstance(upload_url, str)):
            report.bad("LinkedIn did not return an image ID and upload address for the test image")
            return
        uploaded = client.upload_image(upload_url, make_test_image(UPLOAD_TEST_SIZE, UPLOAD_TEST_SIZE))
        if not uploaded.is_success:
            report.bad(f"uploading the test image: LinkedIn answered {uploaded.status_code}")
            return
        report.good(f"test image uploaded ({image_urn}); it is not used in any post")
        for attempt in range(POLL_ATTEMPTS):
            answer = client.get_image(image_urn)
            if answer.status_code == 403:
                report.bad(
                    "this token may NOT read image processing status (403). Real posts would fail "
                    "before posting; the publisher needs the Assets API fallback (a decision is needed)"
                )
                return
            if not answer.is_success:
                report.bad(f"reading the test image's status: LinkedIn answered {answer.status_code}")
                return
            status = answer.json().get("status")
            if status == "AVAILABLE":
                report.good("image processing status is readable (AVAILABLE): the Images API works for real posts")
                return
            if status == "PROCESSING_FAILED":
                report.bad("LinkedIn could not process the test image (PROCESSING_FAILED)")
                return
            if attempt + 1 < POLL_ATTEMPTS:
                sleep(POLL_INTERVAL_S)
        report.bad(f"the test image was still {status} after {POLL_INTERVAL_S * (POLL_ATTEMPTS - 1):.0f} s")
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        report.bad(f"the image upload test failed ({type(error).__name__})")


def make_test_image(width: int, height: int) -> bytes:
    """A small, valid PNG (a soft blue square), generated without any imaging library."""
    row = b"\x00" + bytes((70, 130, 180)) * width  # Filter byte, then RGB pixels.
    pixels = zlib.compress(row * height)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB, no interlace.
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
