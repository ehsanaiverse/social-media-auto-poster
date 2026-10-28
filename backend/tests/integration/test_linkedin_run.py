"""run --real with the real LinkedIn publisher against a fake LinkedIn. No network.

After every scenario, every file the application wrote (log, job database,
checkpoint store, error reports) and everything it printed is searched for the
access token, which must never appear.
"""

import sqlite3
from pathlib import Path

import httpx
import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient
from social_media_auto_poster.platforms.linkedin.publisher import LinkedInPublisher

from linkedin_fake import JPEG, POST_ID, TOKEN, FakeLinkedIn

CAPTION = "Our launch (beta) is live #news"


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    return tmp_path


@pytest.fixture
def settings(base) -> Settings:
    settings = Settings(base_dir=base, data_dir=base / "data")
    incoming = settings.incoming_dir(cli.Platform.LINKEDIN)
    incoming.mkdir(parents=True)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text(CAPTION, encoding="utf-8")
    return settings


def use_linkedin(monkeypatch, fake: FakeLinkedIn) -> None:
    def real_publisher(settings):
        return LinkedInPublisher(LinkedInClient(TOKEN, transport=httpx.MockTransport(fake)), sleep=lambda s: None)

    monkeypatch.setattr(cli, "_real_publisher", real_publisher)


def run() -> int:
    return cli.main(["run", "--real", "--verbose", "--interval", "0"])


def job(settings: Settings) -> tuple:
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute("SELECT status, platform_post_id, destination, errors FROM jobs").fetchone()
    finally:
        conn.close()


def assert_token_never_written(base: Path, captured) -> None:
    """Search every file under the base folder, byte for byte, and everything printed."""
    token = TOKEN.encode("utf-8")
    files = [path for path in base.rglob("*") if path.is_file()]
    assert any(path.name == "autoposter.log" for path in files)
    assert any(path.name == "langgraph_checkpoints.db" for path in files)
    leaks = [str(path) for path in files if token in path.read_bytes()]
    assert leaks == [], f"the token was written to {leaks}"
    assert TOKEN not in captured.out and TOKEN not in captured.err


def test_a_real_run_publishes_records_and_archives(settings, base, monkeypatch, capsys):
    fake = FakeLinkedIn()
    use_linkedin(monkeypatch, fake)

    assert run() == 0

    captured = capsys.readouterr()
    assert "Real mode: 1 post(s) published to LinkedIn in this run." in captured.out
    status, post_id, destination, _ = job(settings)
    assert (status, post_id) == ("PUBLISHED", POST_ID)
    assert Path(destination).parent == settings.archive_dir(cli.Platform.LINKEDIN)
    assert len(fake.calls("POST", "/rest/posts")) == 1
    assert CAPTION not in (base / "logs" / "autoposter.log").read_text(encoding="utf-8")
    assert_token_never_written(base, captured)


def test_an_expired_token_fails_with_a_clear_reason(settings, base, monkeypatch, capsys):
    use_linkedin(monkeypatch, FakeLinkedIn(post=httpx.Response(401)))

    assert run() == 3

    captured = capsys.readouterr()
    status, post_id, destination, errors = job(settings)
    assert status == "FAILED" and post_id is None
    assert "Renew LINKEDIN_ACCESS_TOKEN" in errors
    assert "Renew LINKEDIN_ACCESS_TOKEN" in (Path(destination) / "error-report.txt").read_text(encoding="utf-8")
    assert_token_never_written(base, captured)


@pytest.mark.parametrize("answer", [httpx.Response(503), httpx.ReadTimeout])
def test_an_uncertain_answer_needs_review_and_is_not_retried(settings, base, monkeypatch, capsys, answer):
    fake = FakeLinkedIn(post=answer)
    use_linkedin(monkeypatch, fake)

    assert run() == 3

    captured = capsys.readouterr()
    assert "1 need review: check LinkedIn, then use the resolve command." in captured.out
    status, post_id, destination, errors = job(settings)
    assert status == "NEEDS_REVIEW" and post_id is None
    assert "check LinkedIn before re-posting" in errors
    assert len(fake.calls("POST", "/rest/posts")) == 1
    assert_token_never_written(base, captured)

    assert run() == 0  # Next run: the content is blocked (NEEDS_REVIEW), nothing is posted again.
    assert len(fake.calls("POST", "/rest/posts")) == 1


def test_a_crash_after_posting_keeps_no_token_in_the_checkpoint(settings, base, monkeypatch, capsys):
    fake = FakeLinkedIn()
    use_linkedin(monkeypatch, fake)

    def killed(state, runtime):
        raise SystemExit("process killed after the post was created")

    with monkeypatch.context() as patch:
        patch.setattr(nodes, "record_result", killed)
        with pytest.raises(SystemExit):
            run()
    checkpoint = (base / "database" / "langgraph_checkpoints.db").read_bytes()
    wal = base / "database" / "langgraph_checkpoints.db-wal"
    saved = checkpoint + (wal.read_bytes() if wal.exists() else b"")
    assert POST_ID.encode() in saved  # The saved state really holds the publish result...
    assert TOKEN.encode() not in saved  # ...and never the token.

    assert run() == 0  # Resumed from the checkpoint: recorded as published, nothing posted again.

    captured = capsys.readouterr()
    assert job(settings)[:2] == ("PUBLISHED", POST_ID)
    assert len(fake.calls("POST", "/rest/posts")) == 1
    assert_token_never_written(base, captured)
