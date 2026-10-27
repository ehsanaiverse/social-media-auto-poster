"""Tests for the read-only `jobs` command and duplicate information in `scan`. Temporary folders only."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


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


def db_path(base: Path) -> Path:
    return base / "database" / "app.db"


def add_job(base: Path, job_id: str, *, stem="launch", status=JobStatus.SIMULATED, mode=PublishMode.SIMULATED,
            media: bytes = JPEG_BYTES, caption: bytes = b"Hello", errors=()) -> None:
    conn = open_app_db(db_path(base))
    try:
        JobRepository(conn).create(
            job_id=job_id, platform=Platform.LINKEDIN, mode=mode, stem=stem,
            media_name=f"{stem}.jpg", caption_name=f"{stem}.txt",
            media_sha256=hashlib.sha256(media).hexdigest(), caption_sha256=hashlib.sha256(caption).hexdigest(),
            status=status, errors=errors,
        )
    finally:
        conn.close()


def add_post(incoming: Path, stem: str, media: bytes = JPEG_BYTES, caption: bytes = b"Hello") -> None:
    (incoming / f"{stem}.jpg").write_bytes(media)
    (incoming / f"{stem}.txt").write_bytes(caption)


def jobs(*args: str) -> int:
    return cli.main(["jobs", *args])


def scan() -> int:
    return cli.main(["scan", "--interval", "0"])


# jobs


def test_jobs_without_database_says_so_and_creates_nothing(base, capsys):
    assert jobs() == cli.EXIT_OK

    assert "No jobs yet" in capsys.readouterr().out
    assert not (base / "database").exists()


def test_jobs_lists_newest_first_with_errors(base, capsys):
    add_job(base, "aaaa1111", stem="first", caption=b"1")
    add_job(base, "bbbb2222", stem="second", caption=b"2", status=JobStatus.FAILED, errors=("caption is empty",))

    assert jobs() == cli.EXIT_OK

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["JOB", "CREATED", "(UTC)", "STEM", "MODE", "STATUS"]
    assert lines[1].split()[0] == "bbbb2222" and lines[1].split()[-1] == "FAILED"
    assert lines[2].strip() == "- caption is empty"
    assert lines[3].split()[0] == "aaaa1111"


def test_jobs_last_limits_the_list(base, capsys):
    for n in range(3):
        add_job(base, f"job{n}", caption=str(n).encode(), status=JobStatus.FAILED)

    jobs("--last", "2")

    assert len(capsys.readouterr().out.splitlines()) == 3  # Header + 2 jobs.


def test_jobs_status_filter(base, capsys):
    add_job(base, "sim1", caption=b"1")
    add_job(base, "fail1", caption=b"2", status=JobStatus.FAILED)

    jobs("--status", "failed")  # Case-insensitive.

    out = capsys.readouterr().out
    assert "fail1" in out and "sim1" not in out


def test_jobs_status_filter_with_no_matches(base, capsys):
    add_job(base, "sim1")

    assert jobs("--status", "NEEDS_REVIEW") == cli.EXIT_OK
    assert "No jobs with status NEEDS_REVIEW." in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--status", "BOGUS"], ["--last", "0"], ["--last", "many"]])
def test_jobs_bad_options_are_usage_errors(base, args):
    with pytest.raises(SystemExit) as exc:
        jobs(*args)
    assert exc.value.code == 2


def test_jobs_id_shows_every_detail(base, capsys):
    add_job(base, "8a61f3de4c1e", status=JobStatus.FAILED, errors=("caption is empty",))

    assert jobs("--id", "8A61") == cli.EXIT_OK  # Prefix, case-insensitive.

    out = capsys.readouterr().out
    assert "Job:             8a61f3de4c1e" in out
    assert "Post:            launch (launch.jpg + launch.txt)" in out
    assert "Mode:            simulated" in out
    assert "Status:          FAILED" in out
    assert f"Media SHA-256:   {hashlib.sha256(JPEG_BYTES).hexdigest()}" in out
    assert "Post ID:         -" in out
    assert "  - caption is empty" in out


def test_jobs_id_ambiguous_prefix_lists_matches(base, capsys):
    add_job(base, "abc111", caption=b"1", status=JobStatus.FAILED)
    add_job(base, "abc222", caption=b"2", status=JobStatus.FAILED)

    assert jobs("--id", "abc") == cli.EXIT_SETUP_ERROR

    err = capsys.readouterr().err
    assert "matches 2 jobs" in err and "abc111" in err and "abc222" in err


def test_jobs_id_unknown(base, capsys):
    add_job(base, "abc111")

    assert jobs("--id", "fff") == cli.EXIT_SETUP_ERROR
    assert "no job ID starts with 'fff'" in capsys.readouterr().err


def test_jobs_id_prefix_is_not_a_wildcard(base, capsys):
    add_job(base, "abc111")

    assert jobs("--id", "%") == cli.EXIT_SETUP_ERROR


def test_jobs_refuses_newer_database(base, capsys):
    db_path(base).parent.mkdir()
    conn = sqlite3.connect(db_path(base))
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    assert jobs() == cli.EXIT_SETUP_ERROR
    assert "could not read the job database" in capsys.readouterr().err


def test_jobs_never_changes_the_database(base):
    add_job(base, "abc111")
    before = db_path(base).read_bytes()

    jobs()
    jobs("--id", "abc")

    assert db_path(base).read_bytes() == before


# scan: duplicates and interrupted jobs


def candidate_line(out: str, stem: str) -> str:
    return next(line for line in out.splitlines() if line.strip().startswith(stem + " "))


def test_scan_marks_duplicates_and_new_posts(base, incoming, capsys):
    add_job(base, "8a61f3de4c1e", stem="launch", caption=b"Seen before")
    add_post(incoming, "launch-copy", caption=b"Seen before")
    add_post(incoming, "fresh", caption=b"Never seen")

    assert scan() == cli.EXIT_OK

    out = capsys.readouterr().out
    assert candidate_line(out, "launch-copy").endswith("valid   DUPLICATE of job 8a61f3de (SIMULATED, " + _today(base) + ")")
    assert candidate_line(out, "fresh").endswith("valid   new")


def _today(base: Path) -> str:
    conn = sqlite3.connect(db_path(base))
    created = conn.execute("SELECT created_at FROM jobs").fetchone()[0]
    conn.close()
    return created[:10]


def test_scan_ignores_failed_and_real_mode_history(base, incoming, capsys):
    add_job(base, "failed1", caption=b"A", status=JobStatus.FAILED)
    add_job(base, "real1", caption=b"B", mode=PublishMode.REAL, status=JobStatus.PUBLISHED)
    add_post(incoming, "a", caption=b"A")
    add_post(incoming, "b", caption=b"B")

    scan()

    out = capsys.readouterr().out
    assert candidate_line(out, "a").endswith("valid   new")
    assert candidate_line(out, "b").endswith("valid   new")  # run is simulated; real history does not block it.


def test_scan_without_database_shows_new_and_creates_nothing(base, incoming, capsys):
    add_post(incoming, "launch")

    scan()

    assert candidate_line(capsys.readouterr().out, "launch").endswith("valid   new")
    assert not (base / "database").exists()


def test_scan_does_not_hash_invalid_or_unstable_posts(base, incoming, monkeypatch, capsys):
    add_job(base, "x")
    add_post(incoming, "bad", media=b"not an image")
    add_post(incoming, "copying")
    hashed = []
    real_sha = cli.sha256_file
    monkeypatch.setattr(cli, "sha256_file", lambda p, **kw: hashed.append(p.name) or real_sha(p, **kw))
    monkeypatch.setattr(cli, "is_file_stable", lambda path, interval: path.stem != "copying")

    scan()

    out = capsys.readouterr().out
    assert hashed == []
    assert "INVALID" in candidate_line(out, "bad")
    assert candidate_line(out, "copying").endswith("valid")


def test_scan_reports_interrupted_jobs_without_changing_them(base, incoming, capsys):
    add_job(base, "stuck1", status=JobStatus.PUBLISHING)
    before = db_path(base).read_bytes()

    scan()

    assert "Note: 1 interrupted job(s) will be recovered by the next run." in capsys.readouterr().out
    assert db_path(base).read_bytes() == before


def test_scan_works_when_database_is_unreadable(base, incoming, capsys):
    db_path(base).parent.mkdir()
    conn = sqlite3.connect(db_path(base))
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    add_post(incoming, "launch")

    assert scan() == cli.EXIT_OK

    captured = capsys.readouterr()
    assert candidate_line(captured.out, "launch").endswith("valid   new")
    assert "Duplicate check unavailable" in captured.err
