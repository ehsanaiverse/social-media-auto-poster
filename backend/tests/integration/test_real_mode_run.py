"""run --real and scan --real (Milestone 4, slice 4.1). A fake publisher stands in for LinkedIn; no network."""

import sqlite3
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, Platform, PublishResult

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN


class FakeLinkedIn:
    """Stands in for the real publisher: counts calls and returns a confirmed post."""

    def __init__(self):
        self.published: list[str] = []

    def publish(self, post, /):
        self.published.append(post.stem)
        return PublishResult(success=True, simulated=False, platform_post_id=f"urn:li:share:{len(self.published)}")


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    return tmp_path


@pytest.fixture
def settings(base) -> Settings:
    return Settings(base_dir=base, data_dir=base / "data")


@pytest.fixture
def linkedin(monkeypatch) -> FakeLinkedIn:
    fake = FakeLinkedIn()
    monkeypatch.setattr(cli, "_real_publisher", lambda settings: fake)
    return fake


def add_post(settings: Settings, stem: str, caption: bytes = b"Hello LinkedIn") -> None:
    incoming = settings.incoming_dir(LINKEDIN)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / f"{stem}.jpg").write_bytes(JPEG_BYTES)
    (incoming / f"{stem}.txt").write_bytes(caption)


def run(*extra: str) -> int:
    return cli.main(["run", "--interval", "0", *extra])


def rows(settings: Settings) -> list[tuple[str, str, str]]:
    if not settings.app_db_path.exists():
        return []
    conn = sqlite3.connect(settings.app_db_path)
    try:
        return conn.execute("SELECT stem, mode, status FROM jobs ORDER BY created_at, rowid").fetchall()
    finally:
        conn.close()


# Choosing the mode


def test_real_mode_is_refused_while_no_real_publisher_is_set_up(settings, capsys):
    add_post(settings, "launch")

    assert run("--real") == 1

    captured = capsys.readouterr()
    assert "real publishing is not set up yet; nothing was published" in captured.err
    assert (settings.incoming_dir(LINKEDIN) / "launch.jpg").exists()
    assert rows(settings) == []


def test_a_plain_run_never_asks_for_the_real_publisher(settings, monkeypatch, capsys):
    def must_not_be_called(settings):
        raise AssertionError("the real publisher was requested without --real")

    monkeypatch.setattr(cli, "_real_publisher", must_not_be_called)
    add_post(settings, "launch")

    assert run() == 0

    assert "Simulation mode: nothing was sent to LinkedIn in this run." in capsys.readouterr().out
    assert rows(settings) == [("launch", "simulated", "SIMULATED")]


def test_publish_simulated_without_real_is_a_usage_error(settings, capsys):
    assert run("--publish-simulated") == 2
    assert "--publish-simulated only applies together with --real" in capsys.readouterr().err


def test_real_run_publishes_archives_and_says_so(settings, linkedin, capsys):
    add_post(settings, "launch")

    assert run("--real") == 0

    out = capsys.readouterr().out
    assert "REAL MODE: posts are published to LinkedIn" in out
    assert "Real mode: 1 post(s) published to LinkedIn in this run." in out
    assert linkedin.published == ["launch"]
    assert rows(settings) == [("launch", "real", "PUBLISHED")]
    assert len(list(settings.archive_dir(LINKEDIN).iterdir())) == 1


# Safeguard 1: posts that were only simulated (D33)


def test_real_run_refuses_posts_that_were_only_simulated(settings, linkedin, capsys):
    add_post(settings, "launch")
    add_post(settings, "fresh", caption=b"Never simulated")
    assert run() == 0  # Simulates both.
    add_post(settings, "brand-new", caption=b"Added after the simulation")
    capsys.readouterr()

    assert run("--real") == 1

    out = capsys.readouterr().out
    assert "REFUSED (2) - these posts were only simulated" in out
    assert "fresh" in out and "launch" in out and "brand-new" not in out.split("REFUSED")[1]
    assert "--real --publish-simulated" in out
    assert linkedin.published == []  # Nothing at all was published, not even the new post.
    assert [r for r in rows(settings) if r[1] == "real"] == []


def test_publish_simulated_confirms_and_publishes_them(settings, linkedin, capsys):
    add_post(settings, "launch")
    assert run() == 0

    assert run("--real", "--publish-simulated") == 0

    assert linkedin.published == ["launch"]
    assert ("launch", "real", "PUBLISHED") in rows(settings)


def test_content_already_published_for_real_is_a_duplicate_not_a_refusal(settings, linkedin, capsys):
    add_post(settings, "launch")
    assert run("--real") == 0
    add_post(settings, "launch")  # The same post dropped in again.
    assert run() == 0  # A simulated run handles it once in simulated mode.
    capsys.readouterr()

    assert run("--real") == 0

    out = capsys.readouterr().out
    assert "REFUSED" not in out and "DUPLICATE" in out
    assert linkedin.published == ["launch"]


# Safeguard 2: published posts the job database does not know about (D33)


def test_real_run_refuses_archive_folders_without_real_history(settings, linkedin, capsys):
    (settings.archive_dir(LINKEDIN) / "20261020T100000Z_old-post").mkdir(parents=True)
    add_post(settings, "launch")

    assert run("--real") == 1

    err = capsys.readouterr().err
    assert "REFUSED: archive/ holds 1 published post folder(s)" in err
    assert "Restore your backup of app.db" in err and "20261020T100000Z_old-post" in err
    assert linkedin.published == []
    assert (settings.incoming_dir(LINKEDIN) / "launch.jpg").exists()


def test_archive_folders_are_fine_once_real_history_exists(settings, linkedin):
    add_post(settings, "first", caption=b"first")
    assert run("--real") == 0  # Archives "first" and records a real job.
    add_post(settings, "second", caption=b"second")

    assert run("--real") == 0

    assert linkedin.published == ["first", "second"]


def test_a_refused_real_run_changes_nothing(settings, linkedin, monkeypatch):
    (settings.archive_dir(LINKEDIN) / "20261020T100000Z_old-post").mkdir(parents=True)
    add_post(settings, "launch")
    resumed = []
    monkeypatch.setattr(cli, "resume_interrupted_jobs", lambda *args: resumed.append(args))

    assert run("--real") == 1

    assert resumed == []  # Refused before startup recovery or anything else could change state.


# scan --real


def test_scan_real_shows_what_run_real_would_refuse(settings, capsys):
    add_post(settings, "launch")
    assert run() == 0
    capsys.readouterr()

    assert cli.main(["scan", "--real", "--interval", "0"]) == 0

    out = capsys.readouterr().out
    assert "(duplicate check: real mode)" in out
    assert "new, but already simulated as job" in out and "without --publish-simulated" in out

    assert cli.main(["scan", "--interval", "0"]) == 0
    assert "DUPLICATE of job" in capsys.readouterr().out  # In simulated mode it is a duplicate.


def test_scan_real_notes_archive_folders_without_history(settings, capsys):
    (settings.archive_dir(LINKEDIN) / "20261020T100000Z_old-post").mkdir(parents=True)
    add_post(settings, "launch")

    assert cli.main(["scan", "--real", "--interval", "0"]) == 0

    assert "run --real would refuse: 1 folder(s) in archive/" in capsys.readouterr().out
