"""linkedin-check, and run --real going through the real setup (backend/.env, token check). Fake LinkedIn only."""

import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings
from social_media_auto_poster.platforms.linkedin import check
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient

from linkedin_fake import JPEG, POST_ID, TOKEN, FakeLinkedIn

TODAY = date(2026, 10, 28)


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    monkeypatch.setattr(check, "POLL_INTERVAL_S", 0)
    return tmp_path


@pytest.fixture
def fake(monkeypatch) -> FakeLinkedIn:
    linkedin = FakeLinkedIn()
    monkeypatch.setattr(cli, "_linkedin_client", lambda token: LinkedInClient(token, transport=httpx.MockTransport(linkedin)))
    return linkedin


def write_env(base: Path, created: str | None = "2026-10-01") -> None:
    lines = [f"LINKEDIN_ACCESS_TOKEN={TOKEN}"] + ([f"LINKEDIN_TOKEN_CREATED={created}"] if created else [])
    (base / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_post(base: Path) -> Settings:
    settings = Settings(base_dir=base, data_dir=base / "data")
    incoming = settings.incoming_dir(cli.Platform.LINKEDIN)
    incoming.mkdir(parents=True)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text("Hello LinkedIn", encoding="utf-8")
    return settings


def linkedin_check(*extra: str) -> int:
    return cli.main(["linkedin-check", *extra])


def run_real() -> int:
    return cli.main(["run", "--real", "--interval", "0"])


# linkedin-check


def test_check_without_a_token_says_so(base, fake, capsys):
    assert linkedin_check() == 1
    assert "PROBLEM  LINKEDIN_ACCESS_TOKEN is not set in backend\\.env" in capsys.readouterr().out
    assert fake.requests == []


def test_check_confirms_the_token_and_the_member(base, fake, capsys):
    write_env(base)

    assert linkedin_check() == 0

    out = capsys.readouterr().out
    assert "OK       LinkedIn API version 202610" in out
    assert "OK       access token is set; the LinkedIn access token expires on 2026-11-30 (33 days left)" in out
    assert "posts would be published as X (urn:li:person:abc123)" in out
    assert "image status not checked (add --upload-test" in out
    assert "Ready for run --real." in out and TOKEN not in out
    assert [r.url.path for r in fake.requests] == ["/v2/userinfo"]


def test_upload_test_confirms_image_status_can_be_read_and_never_posts(base, fake, capsys):
    write_env(base)
    fake.image_statuses = ["PROCESSING", "AVAILABLE"]

    assert linkedin_check("--upload-test") == 0

    out = capsys.readouterr().out
    assert "test image uploaded (urn:li:image:" in out and "it is not used in any post" in out
    assert "image processing status is readable (AVAILABLE): the Images API works for real posts" in out
    assert fake.calls("POST", "/rest/posts") == []  # Never a post.
    [upload] = fake.calls("PUT", "/")
    assert upload.content.startswith(b"\x89PNG") and len(upload.content) < 2000


def test_upload_test_reports_when_image_status_cannot_be_read(base, fake, capsys):
    write_env(base)
    fake.image = httpx.Response(403)

    assert linkedin_check("--upload-test") == 1

    out = capsys.readouterr().out
    assert "this token may NOT read image processing status (403)" in out
    assert "Assets API fallback" in out and "Not ready for run --real" in out
    assert fake.calls("POST", "/rest/posts") == []


def test_check_reports_a_rejected_token(base, fake, capsys):
    write_env(base)
    fake.userinfo = httpx.Response(401)

    assert linkedin_check() == 1
    assert "LinkedIn rejected the access token (401)" in capsys.readouterr().out


def test_check_with_an_expired_token_sends_nothing(base, fake, capsys):
    write_env(base, created="2026-08-01")

    assert linkedin_check() == 1

    assert "PROBLEM  the LinkedIn access token expired on 2026-09-30" in capsys.readouterr().out
    assert fake.requests == []


def test_check_warns_when_the_token_expires_soon(base, fake, capsys):
    write_env(base, created="2026-09-02")

    assert linkedin_check() == 0
    assert "WARNING  access token is set; the LinkedIn access token expires on 2026-11-01 (4 day(s) left)" in (
        capsys.readouterr().out
    )


# run --real through the real setup path


def job_rows(settings: Settings) -> list[tuple]:
    if not settings.app_db_path.exists():
        return []
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute("SELECT status, platform_post_id FROM jobs").fetchall()
    finally:
        conn.close()


def test_run_real_publishes_with_the_token_from_the_env_file(base, fake, capsys):
    write_env(base)
    settings = add_post(base)

    assert run_real() == 0

    assert job_rows(settings) == [("PUBLISHED", POST_ID)]
    assert [r.url.path for r in fake.requests].count("/v2/userinfo") == 1  # Checked once, at the start.
    assert TOKEN not in capsys.readouterr().err


@pytest.mark.parametrize(("env", "answer", "message"), [
    ("expired", None, "the LinkedIn access token expired on 2026-09-30"),
    ("rejected", httpx.Response(401), "LinkedIn rejected the access token (401)"),
    ("unreachable", httpx.ConnectError, "reading the LinkedIn member failed (ConnectError)"),
])
def test_run_real_stops_before_anything_changes_when_the_token_is_not_usable(base, fake, capsys, env, answer, message):
    write_env(base, created="2026-08-01" if env == "expired" else "2026-10-01")
    fake.userinfo = answer
    settings = add_post(base)

    assert run_real() == 1

    err = capsys.readouterr().err
    assert message in err and "nothing was published" in err
    assert fake.calls("POST", "/rest/images") == [] and fake.calls("POST", "/rest/posts") == []
    assert (settings.incoming_dir(cli.Platform.LINKEDIN) / "launch.jpg").exists()
    assert job_rows(settings) == []


def test_run_real_warns_when_the_token_expires_soon_and_still_publishes(base, fake, capsys):
    write_env(base, created="2026-09-02")
    settings = add_post(base)

    assert run_real() == 0

    assert "the LinkedIn access token expires on 2026-11-01 (4 day(s) left)" in capsys.readouterr().err
    assert job_rows(settings) == [("PUBLISHED", POST_ID)]


def test_the_real_network_is_blocked_in_tests(base):
    write_env(base)
    # No fake here: the real client is used, and the safety net in conftest.py stops it.
    with pytest.raises(RuntimeError, match="tests must not use the network"):
        linkedin_check()
