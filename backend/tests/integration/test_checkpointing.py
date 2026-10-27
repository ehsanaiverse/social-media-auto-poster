"""Milestone 3: the graph saves checkpoints to their own SQLite store.

Temporary folders only; nothing contacts a platform.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.checkpoints import open_checkpointer, saved_thread_ids
from social_media_auto_poster.graph.state import JobState, WorkflowContext, new_job_state
from social_media_auto_poster.graph.workflow import build_workflow, run_job
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, PublishResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.archive import ERROR_REPORT_NAME, JOB_MARKER_NAME
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN
REAL_SUCCESS = PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:123")


class FakeRealPublisher:
    """Stands in for a real publisher; counts calls and can run a check during the call."""

    def __init__(self, during=None):
        self.during = during
        self.calls = 0

    def publish(self, post, /):
        self.calls += 1
        if self.during is not None:
            self.during()
        return REAL_SUCCESS


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_dir=tmp_path, data_dir=tmp_path / "data")


@pytest.fixture
def repo(settings):
    conn = open_app_db(settings.app_db_path)
    yield JobRepository(conn)
    conn.close()


@pytest.fixture
def saver(settings):
    with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as checkpointer:
        yield checkpointer


def make_context(settings, repo, saver, publisher, mode=None) -> WorkflowContext:
    if mode is None:
        mode = PublishMode.SIMULATED if isinstance(publisher, Simulator) else PublishMode.REAL
    return WorkflowContext(
        publisher=publisher, mode=mode, repository=repo, settings=settings, stability_interval_s=0, checkpointer=saver
    )


def make_candidate(settings: Settings, stem: str = "launch", caption: bytes = b"Hello LinkedIn") -> PostCandidate:
    incoming = settings.incoming_dir(LINKEDIN)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / f"{stem}.jpg").write_bytes(JPEG_BYTES)
    (incoming / f"{stem}.txt").write_bytes(caption)
    return PostCandidate(LINKEDIN, stem, incoming / f"{stem}.jpg", incoming / f"{stem}.txt")


def saved_state(checkpointer, thread_id: str):
    return build_workflow(checkpointer).get_state({"configurable": {"thread_id": thread_id}})


# Saving and deleting threads


def test_a_finished_job_leaves_no_checkpoint_thread(settings, repo, saver):
    final = run_job(make_candidate(settings), make_context(settings, repo, saver, Simulator()))

    assert final["status"] == JobStatus.SIMULATED
    assert saved_thread_ids(saver) == []
    assert repo.get(final["job_id"]).status is JobStatus.SIMULATED  # The record lives in the job database.


def test_a_skipped_duplicate_leaves_no_checkpoint_thread(settings, repo, saver):
    context = make_context(settings, repo, saver, Simulator())
    run_job(make_candidate(settings), context)

    final = run_job(make_candidate(settings), context)

    assert final["duplicate_of"] is not None
    assert saved_thread_ids(saver) == []


def test_the_publish_is_next_checkpoint_is_on_disk_before_publish_is_called(settings, repo, saver):
    seen = []

    def read_from_another_connection():
        # A separate connection only sees committed data, so this proves the save finished.
        with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as other:
            [thread] = saved_thread_ids(other)
            state = saved_state(other, thread)
            seen.append((state.next, state.values["status"]))

    publisher = FakeRealPublisher(during=read_from_another_connection)
    run_job(make_candidate(settings), make_context(settings, repo, saver, publisher))

    assert publisher.calls == 1
    assert seen == [(("publish",), JobStatus.PUBLISHING)]


def test_a_crash_after_publish_leaves_the_thread_with_the_result(settings, repo, saver, monkeypatch):
    def crash(state, runtime):
        raise SystemExit("process killed")

    monkeypatch.setattr(nodes, "record_result", crash)
    with pytest.raises(SystemExit):
        run_job(make_candidate(settings), make_context(settings, repo, saver, FakeRealPublisher()))

    [thread] = saved_thread_ids(saver)
    state = saved_state(saver, thread)
    assert state.next == ("record_result",)
    assert state.values["status"] == JobStatus.PUBLISHED
    assert state.values["platform_post_id"] == "urn:li:share:123"
    assert repo.get(thread).status is JobStatus.PUBLISHING  # Not recorded yet.


# The run command


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    return tmp_path


def run() -> int:
    return cli.main(["run", "--interval", "0"])


def test_run_uses_the_checkpoint_store_and_leaves_no_threads(base):
    make_candidate(Settings(base_dir=base, data_dir=base / "data"))

    assert run() == 0

    checkpoints = base / "database" / "langgraph_checkpoints.db"
    assert checkpoints.is_file()
    conn = sqlite3.connect(checkpoints)
    try:
        assert conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_unreadable_checkpoint_store_stops_the_run_before_anything_happens(base, capsys):
    settings = Settings(base_dir=base, data_dir=base / "data")
    candidate = make_candidate(settings)
    settings.checkpoint_db_path.parent.mkdir(parents=True)
    settings.checkpoint_db_path.write_bytes(b"this is not a SQLite database" * 10)

    assert run() == 1

    assert "Could not open the checkpoint store" in capsys.readouterr().err
    assert candidate.media_path.exists() and candidate.caption_path.exists()
    conn = open_app_db(settings.app_db_path)
    try:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()


# Resume at startup


@pytest.fixture
def base_settings(base) -> Settings:
    return Settings(base_dir=base, data_dir=base / "data")


def crash_job(settings: Settings, candidate: PostCandidate, publisher, *, monkeypatch, node: str, after=False, state=None):
    """Run one job until ``node`` "kills the process"; the stores are then closed, as by a dead process.

    With ``after``, the real node runs first and the crash comes right after it,
    before its checkpoint is saved. Returns the job ID (the thread ID).
    """
    real_node = getattr(nodes, node)

    def crash(state, runtime):  # Every node crashed here takes the runtime.
        if after:
            real_node(state, runtime)
        raise SystemExit(f"process killed in {node}")

    with monkeypatch.context() as patch:
        patch.setattr(nodes, node, crash)
        conn = open_app_db(settings.app_db_path)
        try:
            with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
                context = make_context(settings, JobRepository(conn), saver, publisher)
                with pytest.raises(SystemExit):
                    if state is None:
                        run_job(candidate, context)
                    else:
                        build_workflow(saver).invoke(
                            state, {"configurable": {"thread_id": state["job_id"]}}, context=context, durability="sync"
                        )
                [thread] = saved_thread_ids(saver)
        finally:
            conn.close()
    return thread


def stored(settings: Settings, job_id: str):
    conn = open_app_db(settings.app_db_path)
    try:
        return JobRepository(conn).get(job_id)
    finally:
        conn.close()


def threads_left(settings: Settings) -> list[str]:
    with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
        return saved_thread_ids(saver)


def count_simulator_calls(monkeypatch) -> list:
    calls = []
    real = Simulator.publish
    monkeypatch.setattr(Simulator, "publish", lambda self, post: calls.append(post) or real(self, post))
    return calls


def raise_in_node(error: BaseException):
    def node(state, runtime):
        raise error

    return node


def test_simulated_crash_after_publish_is_resumed_without_publishing_again(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    job_id = crash_job(base_settings, candidate, Simulator(), monkeypatch=monkeypatch, node="record_result")
    assert stored(base_settings, job_id).status is JobStatus.PUBLISHING
    calls = count_simulator_calls(monkeypatch)
    capsys.readouterr()

    assert run() == 0

    out = capsys.readouterr().out
    assert "RESUMED (1) - jobs finished after an earlier crash" in out
    assert "SIMULATED (not published)" in out
    assert calls == []  # Resumed without publishing; the post is now a DUPLICATE of the resumed job.
    assert stored(base_settings, job_id).status is JobStatus.SIMULATED
    assert "RECOVERED" not in out
    assert threads_left(base_settings) == []


def test_real_crash_after_publish_is_recorded_as_published_with_its_post_id(base_settings, monkeypatch):
    candidate = make_candidate(base_settings)
    publisher = FakeRealPublisher()
    job_id = crash_job(base_settings, candidate, publisher, monkeypatch=monkeypatch, node="record_result")

    assert run() == 0

    record = stored(base_settings, job_id)
    assert record.status is JobStatus.PUBLISHED  # Not NEEDS_REVIEW: the checkpoint held the result.
    assert record.platform_post_id == "urn:li:share:123"
    assert record.destination is not None
    assert Path(record.destination).parent == base_settings.archive_dir(LINKEDIN)
    assert (Path(record.destination) / "launch.jpg").exists()
    assert publisher.calls == 1
    assert threads_left(base_settings) == []


@pytest.mark.parametrize("publisher_type", [Simulator, FakeRealPublisher])
def test_crash_inside_publish_is_never_resumed(base_settings, monkeypatch, capsys, publisher_type):
    candidate = make_candidate(base_settings)
    publisher = publisher_type()
    job_id = crash_job(base_settings, candidate, publisher, monkeypatch=monkeypatch, node="publish")
    calls = count_simulator_calls(monkeypatch)

    exit_code = run()

    out = capsys.readouterr().out
    assert "RESUMED" not in out
    assert "RECOVERED (1)" in out
    record = stored(base_settings, job_id)
    if publisher_type is FakeRealPublisher:
        assert record.status is JobStatus.NEEDS_REVIEW and exit_code == 3
        assert publisher.calls == 0  # The crash replaced the call; it was never made or repeated.
    else:
        assert record.status is JobStatus.FAILED and exit_code == 0
        assert len(calls) == 1  # Processed again as a new job by this run, as before Milestone 3.
    assert threads_left(base_settings) == []


@pytest.mark.parametrize("publisher_type", [Simulator, FakeRealPublisher])
def test_crash_before_publish_is_failed_not_needs_review(base_settings, monkeypatch, capsys, publisher_type):
    candidate = make_candidate(base_settings)
    # mark_publishing committed the PUBLISHING row, then the process died before its checkpoint was saved.
    job_id = crash_job(
        base_settings, candidate, publisher_type(), monkeypatch=monkeypatch, node="mark_publishing", after=True
    )
    assert stored(base_settings, job_id).status is JobStatus.PUBLISHING

    assert run() == 0

    out = capsys.readouterr().out
    assert "interrupted by a crash before publishing; nothing was sent" in out
    assert stored(base_settings, job_id).status is JobStatus.FAILED
    assert "SIMULATED (not published)" in out  # The post was processed again by this run.
    assert threads_left(base_settings) == []


def test_crash_before_any_record_just_drops_the_thread(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    job_id = crash_job(base_settings, candidate, Simulator(), monkeypatch=monkeypatch, node="check_duplicate")
    assert stored(base_settings, job_id) is None

    assert run() == 0

    out = capsys.readouterr().out
    assert "RESUMED" not in out and "RECOVERED" not in out
    assert "SIMULATED (not published)" in out
    assert threads_left(base_settings) == []


# Unknown or unreadable checkpoints are not resumed


def test_corrupt_checkpoint_is_not_resumed_and_reconciliation_decides(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    publisher = FakeRealPublisher()
    job_id = crash_job(base_settings, candidate, publisher, monkeypatch=monkeypatch, node="record_result")
    conn = sqlite3.connect(base_settings.checkpoint_db_path)
    conn.execute("UPDATE checkpoints SET checkpoint = X'00FF00'")
    conn.commit()
    conn.close()

    assert run() == 3

    captured = capsys.readouterr()
    assert f"Checkpoint not resumed: job_id={job_id} problem=unreadable" in captured.err
    assert "RESUMED" not in captured.out
    assert stored(base_settings, job_id).status is JobStatus.NEEDS_REVIEW  # The safe answer without the checkpoint.
    assert publisher.calls == 1
    assert threads_left(base_settings) == []


def test_checkpoint_in_an_older_format_is_not_resumed(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    old_state = new_job_state("old-format-job", candidate, PublishMode.REAL)
    del old_state["folder_name"]  # Saved before this field existed.
    crash_job(
        base_settings, candidate, FakeRealPublisher(), monkeypatch=monkeypatch, node="record_result", state=old_state
    )

    assert run() == 3

    err = capsys.readouterr().err
    assert "Checkpoint not resumed: job_id=old-format-job problem=saved state is missing folder_name" in err
    assert stored(base_settings, "old-format-job").status is JobStatus.NEEDS_REVIEW
    assert threads_left(base_settings) == []


def test_checkpoint_from_a_renamed_node_is_not_resumed(base_settings, capsys):
    candidate = make_candidate(base_settings)
    state = new_job_state("renamed-job", candidate, PublishMode.REAL)
    conn = open_app_db(base_settings.app_db_path)
    JobRepository(conn).create(
        job_id="renamed-job", platform=LINKEDIN, mode=PublishMode.REAL, stem="launch", media_name="launch.jpg",
        caption_name="launch.txt", media_sha256="a" * 64, caption_sha256="b" * 64, status=JobStatus.PUBLISHING,
    )
    conn.close()
    # A graph from an older version, whose next step was called "store_result".
    old = StateGraph(JobState)
    old.add_node("first", lambda s: {"status": JobStatus.PUBLISHED.value})
    old.add_node("store_result", raise_in_node(SystemExit("process killed")))
    old.add_edge(START, "first")
    old.add_edge("first", "store_result")
    old.add_edge("store_result", END)
    with open_checkpointer(base_settings.checkpoint_db_path, app_db_path=base_settings.app_db_path) as saver:
        with pytest.raises(SystemExit):
            old.compile(checkpointer=saver).invoke(
                state, {"configurable": {"thread_id": "renamed-job"}}, durability="sync"
            )

    assert run() == 3

    captured = capsys.readouterr()
    assert "Checkpoint not resumed: job_id=renamed-job" in captured.err
    assert "RESUMED" not in captured.out
    assert stored(base_settings, "renamed-job").status is JobStatus.NEEDS_REVIEW
    assert threads_left(base_settings) == []


def test_a_failing_resume_keeps_the_thread_and_the_next_run_finishes_it(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    job_id = crash_job(base_settings, candidate, FakeRealPublisher(), monkeypatch=monkeypatch, node="record_result")

    real_record = nodes.record_result

    def broken_for_the_resumed_job(state, runtime):
        if state["job_id"] == job_id:
            raise RuntimeError("a bug")
        return real_record(state, runtime)

    with monkeypatch.context() as patch:
        patch.setattr(nodes, "record_result", broken_for_the_resumed_job)
        assert run() == 3
    out = capsys.readouterr().out
    assert "ERROR (unexpected)" in out and "resume failed; kept for the next run" in out
    assert stored(base_settings, job_id).status is JobStatus.PUBLISHING  # Not reconciled: resume can still finish it.
    assert threads_left(base_settings) == [job_id]

    assert run() == 0

    assert stored(base_settings, job_id).status is JobStatus.PUBLISHED
    assert threads_left(base_settings) == []


# Crashes while moving files are finished by the resume


def crash_while(settings: Settings, candidate: PostCandidate, publisher, *, monkeypatch, setup) -> str:
    """Run one job with ``setup(patch)`` applied; it must "kill the process". Returns the job ID."""
    with monkeypatch.context() as patch:
        setup(patch)
        conn = open_app_db(settings.app_db_path)
        try:
            with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
                with pytest.raises(SystemExit):
                    run_job(candidate, make_context(settings, JobRepository(conn), saver, publisher))
                [thread] = saved_thread_ids(saver)
        finally:
            conn.close()
    return thread


def die_when_moving(source: Path):
    """os.replace that "kills the process" when moving ``source``: no rollback, like a real crash."""
    real_replace = os.replace

    def replace(src, dst):
        if Path(src) == source:
            raise SystemExit("process killed mid-move")
        real_replace(src, dst)

    return replace


def die(*args, **kwargs):
    raise SystemExit("process killed")


def files_in(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_archive_crash_between_image_and_caption_is_finished(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    job_id = crash_while(
        base_settings, candidate, FakeRealPublisher(), monkeypatch=monkeypatch,
        setup=lambda patch: patch.setattr(os, "replace", die_when_moving(candidate.caption_path)),
    )
    [folder] = base_settings.archive_dir(LINKEDIN).iterdir()
    assert files_in(folder) == [JOB_MARKER_NAME, "launch.jpg"] and candidate.caption_path.exists()  # Split.
    assert stored(base_settings, job_id).status is JobStatus.PUBLISHED  # Recorded before the move.

    assert run() == 0

    assert "RESUMED (1)" in capsys.readouterr().out
    assert files_in(folder) == [JOB_MARKER_NAME, "launch.jpg", "launch.txt"]
    assert not candidate.media_path.exists() and not candidate.caption_path.exists()
    record = stored(base_settings, job_id)
    assert record.status is JobStatus.PUBLISHED and record.destination == str(folder) and record.errors == ()
    assert threads_left(base_settings) == []


def test_archive_crash_after_both_moves_before_the_checkpoint_is_finished(base_settings, monkeypatch):
    candidate = make_candidate(base_settings)
    job_id = crash_while(
        base_settings, candidate, FakeRealPublisher(), monkeypatch=monkeypatch,
        setup=lambda patch: patch.setattr(nodes, "_update_after_publish", die),
    )
    [folder] = base_settings.archive_dir(LINKEDIN).iterdir()
    assert files_in(folder) == [JOB_MARKER_NAME, "launch.jpg", "launch.txt"]
    assert stored(base_settings, job_id).destination is None  # Not recorded before the crash.

    assert run() == 0

    assert [p.name for p in base_settings.archive_dir(LINKEDIN).iterdir()] == [folder.name]  # No second folder.
    record = stored(base_settings, job_id)
    assert record.status is JobStatus.PUBLISHED and record.destination == str(folder) and record.errors == ()


def test_archive_split_that_cannot_be_finished_stays_published(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings)
    job_id = crash_while(
        base_settings, candidate, FakeRealPublisher(), monkeypatch=monkeypatch,
        setup=lambda patch: patch.setattr(os, "replace", die_when_moving(candidate.caption_path)),
    )
    candidate.caption_path.unlink()  # Someone deleted the caption before the next run.

    assert run() == 3

    out = capsys.readouterr().out
    assert "manual repair" in out
    record = stored(base_settings, job_id)
    assert record.status is JobStatus.PUBLISHED  # Never changed to FAILED.
    assert record.platform_post_id == "urn:li:share:123"
    assert any("published, but could not move files to archive/" in e for e in record.errors)
    assert threads_left(base_settings) == []


def test_failed_post_crash_between_image_and_caption_is_finished(base_settings, monkeypatch, capsys):
    candidate = make_candidate(base_settings, caption=b"   ")  # Invalid: whitespace-only caption.
    job_id = crash_while(
        base_settings, candidate, Simulator(), monkeypatch=monkeypatch,
        setup=lambda patch: patch.setattr(os, "replace", die_when_moving(candidate.caption_path)),
    )
    assert stored(base_settings, job_id) is None  # Validation failures are recorded after the move.

    assert run() == 3

    assert "RESUMED (1)" in capsys.readouterr().out
    [folder] = base_settings.failed_dir(LINKEDIN).iterdir()
    assert files_in(folder) == [JOB_MARKER_NAME, ERROR_REPORT_NAME, "launch.jpg", "launch.txt"]
    record = stored(base_settings, job_id)
    assert record.status is JobStatus.FAILED and record.destination == str(folder)
    assert threads_left(base_settings) == []


def test_failed_post_crash_after_both_moves_before_the_checkpoint_is_finished(base_settings, monkeypatch):
    candidate = make_candidate(base_settings, caption=b"   ")
    job_id = crash_while(
        base_settings, candidate, Simulator(), monkeypatch=monkeypatch,
        setup=lambda patch: patch.setattr(nodes, "write_error_report", die),
    )
    [folder] = base_settings.failed_dir(LINKEDIN).iterdir()
    assert files_in(folder) == [JOB_MARKER_NAME, "launch.jpg", "launch.txt"]  # Moved; no report yet.

    assert run() == 3

    assert [p.name for p in base_settings.failed_dir(LINKEDIN).iterdir()] == [folder.name]
    assert files_in(folder) == [JOB_MARKER_NAME, ERROR_REPORT_NAME, "launch.jpg", "launch.txt"]
    assert "caption is empty" in (folder / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert stored(base_settings, job_id).status is JobStatus.FAILED


# Real process death


def test_a_killed_process_is_resumed_by_the_next_run(base_settings, capsys):
    make_candidate(base_settings)
    # A separate Python process runs the real `run` command and dies (os._exit: no cleanup at all)
    # as soon as record_result starts, i.e. after the simulated publish returned.
    script = (
        "import os, sys\n"
        "from social_media_auto_poster import main as cli\n"
        "from social_media_auto_poster.graph import nodes\n"
        "nodes.record_result = lambda state, runtime: os._exit(70)\n"
        "sys.exit(cli.main(['run', '--interval', '0']))\n"
    )
    killed = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, text=True)
    assert killed.returncode == 70, killed.stderr
    [job_id] = threads_left(base_settings)
    assert stored(base_settings, job_id).status is JobStatus.PUBLISHING

    assert run() == 0

    assert "RESUMED (1)" in capsys.readouterr().out
    assert stored(base_settings, job_id).status is JobStatus.SIMULATED
    assert threads_left(base_settings) == []
