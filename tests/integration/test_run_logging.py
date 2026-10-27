"""Tests for the run log: run_id, start/finish lines, skipped-job lines, mode, and privacy."""

import re
import sqlite3
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.workflow import run_job

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
SECRET = "SECRET-CAPTION-TEXT-98765"


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    return tmp_path


@pytest.fixture
def incoming(base) -> Path:
    folder = base / "data" / "LinkedIn" / "incoming"
    folder.mkdir(parents=True)
    return folder


def add_post(incoming: Path, stem: str, media: bytes = JPEG_BYTES, caption: str | None = None) -> None:
    (incoming / f"{stem}.jpg").write_bytes(media)
    (incoming / f"{stem}.txt").write_text(caption or f"{SECRET} {stem}", encoding="utf-8")


def run() -> int:
    return cli.main(["run", "--interval", "0"])


def log_lines(base: Path) -> list[str]:
    return (base / "logs" / "autoposter.log").read_text(encoding="utf-8").splitlines()


def run_ids(lines: list[str]) -> list[str]:
    return [re.search(r" run_id=(\S+) ", line).group(1) for line in lines]


def stored_job_ids(base: Path) -> set[str]:
    conn = sqlite3.connect(base / "database" / "app.db")
    ids = {row[0] for row in conn.execute("SELECT job_id FROM jobs")}
    conn.close()
    return ids


def test_run_started_is_first_and_run_finished_is_last(base, incoming):
    add_post(incoming, "launch")

    run()

    lines = log_lines(base)
    assert "Run started: platform=LinkedIn mode=simulated" in lines[0]
    assert "Run finished:" in lines[-1]
    assert lines.index(next(l for l in lines if "Scan complete" in l)) > 0


def test_every_line_of_a_run_has_the_same_run_id_and_runs_differ(base, incoming):
    add_post(incoming, "launch")

    run()
    first_count = len(log_lines(base))
    run()

    lines = log_lines(base)
    first, second = run_ids(lines[:first_count]), run_ids(lines[first_count:])
    assert len(set(first)) == 1 and len(set(second)) == 1
    assert first[0] != second[0]
    assert re.fullmatch(r"[0-9a-f]{8}", first[0])


def test_run_finished_has_counts_exit_code_and_duration(base, incoming, monkeypatch):
    add_post(incoming, "good")
    add_post(incoming, "bad", media=b"not an image")
    (incoming / "lonely.jpg").write_bytes(JPEG_BYTES)
    for name in ("banner.jpg", "banner.png", "banner.txt"):
        (incoming / name).write_bytes(JPEG_BYTES)
    run()
    add_post(incoming, "copying")
    monkeypatch.setattr(nodes, "is_file_stable", lambda path, interval: path.stem != "copying")

    run()  # good is now a duplicate; bad was moved to failed/.

    finished = log_lines(base)[-1]
    assert (
        "simulated=0 published=0 failed=0 needs_review=0 duplicates=1 waiting=1 "
        "pending=1 ambiguous=1 recovered=0 errors=0 exit_code=0 duration_s=" in finished
    )


def test_first_run_counts_and_exit_code_3(base, incoming):
    add_post(incoming, "good")
    add_post(incoming, "bad", media=b"not an image")

    assert run() == cli.EXIT_JOB_PROBLEMS

    assert "simulated=1 published=0 failed=1 " in log_lines(base)[-1]
    assert "exit_code=3" in log_lines(base)[-1]


def test_run_finished_is_logged_on_setup_error(base, incoming):
    (base / "database").mkdir()
    conn = sqlite3.connect(base / "database" / "app.db")
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    assert run() == cli.EXIT_SETUP_ERROR

    lines = log_lines(base)
    assert "Run started" in lines[0]
    assert "Run finished:" in lines[-1] and "exit_code=1" in lines[-1]


def test_duplicate_logs_exactly_one_skipped_line_and_no_unstored_job_id(base, incoming):
    add_post(incoming, "launch")
    run()
    original = next(iter(stored_job_ids(base)))
    second_run_start = len(log_lines(base))

    run()

    second_run = log_lines(base)[second_run_start:]
    launch_lines = [l for l in second_run if "stem=launch" in l]
    assert len(launch_lines) == 1
    assert "status=SKIPPED result=DUPLICATE" in launch_lines[0]
    assert f"duplicate_of={original}" in launch_lines[0]
    assert "job_id=" not in launch_lines[0]


def test_waiting_logs_one_skipped_line_without_job_id(base, incoming, monkeypatch):
    add_post(incoming, "launch")
    monkeypatch.setattr(nodes, "is_file_stable", lambda path, interval: False)

    run()

    launch_lines = [l for l in log_lines(base) if "stem=launch" in l]
    assert len(launch_lines) == 1
    assert "status=SKIPPED result=WAITING" in launch_lines[0]
    assert "job_id=" not in launch_lines[0]


def test_every_logged_job_id_is_stored_in_the_database(base, incoming):
    add_post(incoming, "good")
    add_post(incoming, "bad", media=b"not an image")
    run()
    run()

    logged = {m.group(1) for line in log_lines(base) for m in re.finditer(r"\bjob_id=([0-9a-f]+)", line)}
    assert logged and logged <= stored_job_ids(base)


def test_every_job_line_has_mode(base, incoming):
    add_post(incoming, "good")
    add_post(incoming, "bad", media=b"not an image")
    run()
    run()

    job_lines = [l for l in log_lines(base) if "stem=" in l]
    assert job_lines and all("mode=simulated" in l for l in job_lines)


def test_caption_text_is_never_logged(base, incoming):
    add_post(incoming, "good")
    add_post(incoming, "bad", media=b"not an image")
    run()
    run()

    assert SECRET not in (base / "logs" / "autoposter.log").read_text(encoding="utf-8")


def test_header_is_printed_before_any_job_runs(base, incoming, monkeypatch, capsys):
    add_post(incoming, "launch")
    seen_before_job = []

    def spy(candidate, context):
        seen_before_job.append(capsys.readouterr().out)
        return run_job(candidate, context)

    monkeypatch.setattr(cli, "run_job", spy)

    run()

    assert seen_before_job[0].startswith("Processing LinkedIn incoming:")


def test_terminal_lines_stay_short_without_run_id(base, incoming, capsys):
    add_post(incoming, "bad", media=b"not an image")

    run()

    err = capsys.readouterr().err
    assert "Validation failed" in err
    assert "run_id=" not in err
