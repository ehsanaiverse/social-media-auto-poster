"""End-to-end tests of ``main run`` against a temporary data folder. Publishing is always simulated."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import load_settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.models import Platform
from social_media_auto_poster.services.run_lock import single_run_lock

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    """Run from an empty temporary base folder with its own data folder."""
    monkeypatch.chdir(tmp_path)
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


def add_post(folder: Path, stem: str, media: bytes = JPEG_BYTES, caption: bytes = b"Hello LinkedIn", ext=".jpg"):
    (folder / f"{stem}{ext}").write_bytes(media)
    (folder / f"{stem}.txt").write_bytes(caption)


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
    assert "Simulation mode: nothing was sent to LinkedIn." in out

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


def test_second_run_simulates_again_until_milestone_2(incoming, finals):
    add_post(incoming, "launch")

    run()
    run()

    assert [f["status"] for f in finals] == ["SIMULATED", "SIMULATED"]
    assert finals[0]["job_id"] != finals[1]["job_id"]


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
