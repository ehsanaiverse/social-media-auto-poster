"""End-to-end tests of ``main run`` against a temporary data folder and database.

Publishing is always simulated.
"""

import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import load_settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository
from social_media_auto_poster.services.run_lock import single_run_lock

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    """Use an empty temporary base folder with its own data folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    return tmp_path


@pytest.fixture
def incoming(base) -> Path:
    folder = base / "data" / "LinkedIn" / "incoming"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def finals(monkeypatch) -> list[dict]:
    """Capture each job's final state while still running the real graph."""
    captured: list[dict] = []

    def spy(candidate, context):
        final = run_job(candidate, context)
        captured.append(final)
        return final

    monkeypatch.setattr(cli, "run_job", spy)
    return captured


def add_post(folder: Path, stem: str, media: bytes = JPEG_BYTES, caption: bytes | None = None, ext=".jpg"):
    # Default captions differ per stem: posts with identical bytes are duplicates of each other.
    (folder / f"{stem}{ext}").write_bytes(media)
    (folder / f"{stem}.txt").write_bytes(caption if caption is not None else f"Hello from {stem}".encode())


def run(*extra: str) -> int:
    return cli.main(["run", "--interval", "0", *extra])


def test_mixed_folder_end_to_end(base, incoming, capsys):
    add_post(incoming, "launch")
    add_post(incoming, "animated", media=b"GIF89a", ext=".gif")
    (incoming / "lonely.jpg").write_bytes(JPEG_BYTES)
    for name in ("banner.jpg", "banner.png", "banner.txt"):
        (incoming / name).write_bytes(JPEG_BYTES)

    exit_code = run()

    out = capsys.readouterr().out
    assert exit_code == cli.EXIT_JOB_PROBLEMS
    assert "JOBS (2)" in out
    assert "SIMULATED (not published)" in out
    assert "unsupported image type '.gif'" in out
    assert "PENDING (1)" in out
    assert "AMBIGUOUS (1)" in out
    assert "Simulation mode: nothing was sent to LinkedIn in this run." in out

    assert (incoming / "launch.jpg").exists()  # Simulated: stays in incoming.
    assert not (incoming / "animated.gif").exists()  # Invalid: moved to failed.
    [failed_folder] = (base / "data" / "LinkedIn" / "failed").iterdir()
    assert (failed_folder / "animated.gif").exists()
    assert (failed_folder / "error-report.txt").exists()
    assert (incoming / "lonely.jpg").exists()
    assert (incoming / "banner.png").exists()


def test_all_valid_posts_exit_0(incoming, finals):
    add_post(incoming, "one")
    add_post(incoming, "two")

    assert run() == cli.EXIT_OK
    assert [f["status"] for f in finals] == ["SIMULATED", "SIMULATED"]


def test_simulation_never_produces_published_status(base, incoming, finals):
    for stem in ("a", "b", "c"):
        add_post(incoming, stem)
    add_post(incoming, "bad", caption=b"   ")

    run()

    statuses = {f["status"] for f in finals}
    assert statuses == {"SIMULATED", "FAILED"}
    assert "PUBLISHED" not in statuses
    assert all(f["platform_post_id"] is None for f in finals)
    assert all(f["simulated"] is True for f in finals if f["status"] == "SIMULATED")
    assert list((base / "data" / "LinkedIn" / "archive").iterdir()) == []


def job_rows(base: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(base / "database" / "app.db")
    rows = conn.execute("SELECT stem, status FROM jobs ORDER BY rowid").fetchall()  # Insertion order.
    conn.close()
    return rows


def test_second_run_reports_duplicate_and_adds_no_row(base, incoming, finals, capsys):
    add_post(incoming, "launch")

    assert run() == cli.EXIT_OK
    first_job = finals[0]["job_id"]
    capsys.readouterr()

    assert run() == cli.EXIT_OK

    out = capsys.readouterr().out
    assert finals[1]["duplicate_of"] == first_job
    [line] = [line for line in out.splitlines() if line.strip().startswith("launch")]
    assert "DUPLICATE" in line
    assert f"of job {first_job[:8]} (SIMULATED, " in line
    assert "Duplicates stay in incoming/; delete them when you no longer need them." in out
    assert job_rows(base) == [("launch", "SIMULATED")]
    assert (incoming / "launch.jpg").exists()


def test_same_content_under_another_name_is_a_duplicate(base, incoming, finals):
    add_post(incoming, "launch", caption=b"Same words")
    add_post(incoming, "launch-copy", caption=b"Same words")

    run()

    by_stem = {f["stem"]: f for f in finals}
    assert by_stem["launch"]["status"] == "SIMULATED"
    assert by_stem["launch-copy"]["duplicate_of"] == by_stem["launch"]["job_id"]
    assert job_rows(base) == [("launch", "SIMULATED")]


def test_edited_caption_is_a_new_job(base, incoming, finals):
    add_post(incoming, "launch", caption=b"First version")
    run()

    (incoming / "launch.txt").write_bytes(b"Second version")
    run()

    assert [f["status"] for f in finals] == ["SIMULATED", "SIMULATED"]
    assert finals[1]["duplicate_of"] is None
    assert len(job_rows(base)) == 2


def test_fixed_failed_post_is_processed_again(base, incoming, finals):
    add_post(incoming, "launch", caption=b"   ")
    run()
    [failed_folder] = (base / "data" / "LinkedIn" / "failed").iterdir()

    # The user fixes the caption and moves the post back.
    (failed_folder / "launch.jpg").replace(incoming / "launch.jpg")
    (incoming / "launch.txt").write_bytes(b"Now with a caption")
    run()

    assert [f["status"] for f in finals] == ["FAILED", "SIMULATED"]
    assert job_rows(base) == [("launch", "FAILED"), ("launch", "SIMULATED")]


def test_unchanged_failed_post_moved_back_is_not_blocked(base, incoming, finals):
    add_post(incoming, "launch", media=b"not an image")
    run()
    [failed_folder] = (base / "data" / "LinkedIn" / "failed").iterdir()
    for name in ("launch.jpg", "launch.txt"):
        (failed_folder / name).replace(incoming / name)

    run()

    assert [f["status"] for f in finals] == ["FAILED", "FAILED"]  # Fails again; never reported as a duplicate.
    assert all(f["duplicate_of"] is None for f in finals)


def test_run_creates_the_database_but_scan_does_not(base, incoming):
    add_post(incoming, "launch")

    assert cli.main(["scan", "--interval", "0"]) == cli.EXIT_OK
    assert not (base / "database").exists()

    run()
    assert (base / "database" / "app.db").exists()


def test_database_newer_than_the_code_stops_the_run(base, incoming, finals, capsys):
    add_post(incoming, "launch")
    (base / "database").mkdir()
    conn = sqlite3.connect(base / "database" / "app.db")
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    exit_code = run()

    assert exit_code == cli.EXIT_SETUP_ERROR
    assert "Could not open the job database" in capsys.readouterr().err
    assert finals == []
    assert (incoming / "launch.jpg").exists()


def test_empty_inbox_has_nothing_to_process(incoming, capsys):
    assert run() == cli.EXIT_OK
    assert "Nothing to process." in capsys.readouterr().out


def test_run_creates_missing_data_folders(base):
    assert run() == cli.EXIT_OK
    for platform in ("LinkedIn", "Facebook", "Instagram"):
        for folder in ("incoming", "archive", "failed"):
            assert (base / "data" / platform / folder).is_dir()


def test_unexpected_error_in_one_job_does_not_stop_others(incoming, monkeypatch, capsys):
    add_post(incoming, "bad")
    add_post(incoming, "good")

    def flaky(candidate, context):
        if candidate.stem == "bad":
            raise RuntimeError("bug")
        return run_job(candidate, context)

    monkeypatch.setattr(cli, "run_job", flaky)

    exit_code = run()

    out = capsys.readouterr().out
    assert exit_code == cli.EXIT_JOB_PROBLEMS
    assert "ERROR (unexpected)" in out
    assert "SIMULATED (not published)" in out


def test_log_file_has_one_line_per_job_and_no_caption_text(base, incoming, finals):
    secret = "SECRET-CAPTION-TEXT-12345"
    add_post(incoming, "launch", caption=secret.encode())
    add_post(incoming, "broken", media=b"not an image", caption=secret.encode())

    run()

    log = (base / "logs" / "autoposter.log").read_text(encoding="utf-8")
    finished = [line for line in log.splitlines() if "Job finished" in line]
    assert len(finished) == 2
    for final in finals:
        assert any(f"job_id={final['job_id']}" in line and "platform=LinkedIn" in line for line in finished)
    assert secret not in log


def test_log_file_rotates(base, incoming, monkeypatch):
    monkeypatch.setattr(cli, "LOG_MAX_BYTES", 300)
    add_post(incoming, "launch")

    run()
    run()

    assert (base / "logs" / "autoposter.log.1").exists()


def test_file_still_copying_is_waiting_and_untouched(incoming, monkeypatch, capsys):
    add_post(incoming, "launch")
    monkeypatch.setattr(nodes, "is_file_stable", lambda path, interval: False)

    exit_code = run()

    assert exit_code == cli.EXIT_OK
    assert "WAITING (still copying)" in capsys.readouterr().out
    assert (incoming / "launch.jpg").exists()


def test_second_run_is_refused_while_lock_is_held(base, incoming, finals, capsys):
    add_post(incoming, "launch")

    with single_run_lock(load_settings().lock_file):
        exit_code = run()

    assert exit_code == cli.EXIT_LOCKED
    assert "Another run is in progress" in capsys.readouterr().err
    assert finals == []  # No job ran.


def test_lock_is_released_when_holder_crashes(base, incoming):
    # A separate process takes the lock and dies without releasing it.
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from social_media_auto_poster.services.run_lock import single_run_lock
        with single_run_lock(Path({str(load_settings().lock_file)!r})):
            os._exit(1)
        """
    )
    crashed = subprocess.run([sys.executable, "-c", script], check=False)
    assert crashed.returncode == 1
    assert load_settings().lock_file.exists()  # The file stays behind...

    assert run() == cli.EXIT_OK  # ...but the OS released the lock.


def test_scan_does_not_take_the_lock(base, incoming):
    with single_run_lock(load_settings().lock_file):
        assert cli.main(["scan", "--interval", "0"]) == cli.EXIT_OK


def test_log_timestamps_are_utc_and_failures_logged_with_final_status(base, incoming):
    add_post(incoming, "broken", media=b"not an image")

    run()

    log = (base / "logs" / "autoposter.log").read_text(encoding="utf-8")
    warning = next(line for line in log.splitlines() if "Validation failed" in line)
    assert "status=FAILED" in warning
    timestamp = warning.split(" ", 1)[0]
    assert timestamp.endswith("Z") and "T" in timestamp


# Crash recovery (Slice 2.3)


def seed_stuck_job(base: Path, incoming: Path, stem: str, mode: PublishMode) -> str:
    """Record a post as PUBLISHING, exactly as a run that died mid-publish leaves it."""
    conn = open_app_db(base / "database" / "app.db")
    try:
        JobRepository(conn).create(
            job_id=f"stuck-{stem}", platform=LINKEDIN, mode=mode, stem=stem,
            media_name=f"{stem}.jpg", caption_name=f"{stem}.txt",
            media_sha256=sha256_file(incoming / f"{stem}.jpg"), caption_sha256=sha256_file(incoming / f"{stem}.txt"),
            status=JobStatus.PUBLISHING,
        )
    finally:
        conn.close()
    return f"stuck-{stem}"


def test_crash_mid_publish_is_recovered_and_processed_on_next_run(base, incoming, monkeypatch, capsys):
    add_post(incoming, "launch")

    def process_killed(self, post, /):
        raise SystemExit("process killed mid-publish")

    monkeypatch.setattr(Simulator, "publish", process_killed)
    with pytest.raises(SystemExit):
        run()
    assert job_rows(base) == [("launch", "PUBLISHING")]  # Left behind by the "crash".
    monkeypatch.undo()
    monkeypatch.chdir(base)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(base))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    capsys.readouterr()

    exit_code = run()

    out = capsys.readouterr().out
    assert exit_code == cli.EXIT_OK
    assert "RECOVERED (1) - jobs interrupted by an earlier crash" in out
    assert "(simulated) simulated publish was interrupted by a crash; nothing was sent" in out
    assert "SIMULATED (not published)" in out  # Processed again in the same run.
    assert job_rows(base) == [("launch", "FAILED"), ("launch", "SIMULATED")]


def test_real_mode_stuck_job_needs_review_and_is_never_processed_again(base, incoming, finals, capsys):
    add_post(incoming, "launch")
    run_id = seed_stuck_job(base, incoming, "launch", PublishMode.REAL)

    exit_code = run()

    out = capsys.readouterr().out
    assert exit_code == cli.EXIT_JOB_PROBLEMS
    assert "RECOVERED (1)" in out
    assert "NEEDS_REVIEW" in out and "check the platform before re-posting" in out
    assert finals == []  # Its files were moved to failed/, so nothing was processed.
    assert not (incoming / "launch.jpg").exists()
    [folder] = (base / "data" / "LinkedIn" / "failed").iterdir()
    assert "Check LinkedIn before re-posting" in (folder / "error-report.txt").read_text(encoding="utf-8")
    assert job_rows(base) == [("launch", "NEEDS_REVIEW")]
    assert run_id == "stuck-launch"


def test_next_run_after_recovery_has_nothing_to_recover(base, incoming, capsys):
    add_post(incoming, "launch")
    seed_stuck_job(base, incoming, "launch", PublishMode.SIMULATED)
    run()
    capsys.readouterr()

    run()

    assert "RECOVERED" not in capsys.readouterr().out


def test_scan_never_reconciles(base, incoming):
    add_post(incoming, "launch")
    seed_stuck_job(base, incoming, "launch", PublishMode.SIMULATED)

    assert cli.main(["scan", "--interval", "0"]) == cli.EXIT_OK

    assert job_rows(base) == [("launch", "PUBLISHING")]


def test_recovery_database_failure_stops_the_run(base, incoming, finals, monkeypatch, capsys):
    add_post(incoming, "launch")

    def broken(repository, settings, **_):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(cli, "reconcile_interrupted_jobs", broken)

    assert run() == cli.EXIT_SETUP_ERROR
    assert finals == []
    assert "Could not recover interrupted jobs" in capsys.readouterr().err
