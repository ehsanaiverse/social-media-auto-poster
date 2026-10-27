"""Milestone 3: the graph saves checkpoints to their own SQLite store.

Temporary folders only; nothing contacts a platform.
"""

import sqlite3
from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.checkpoints import open_checkpointer, saved_thread_ids
from social_media_auto_poster.graph.state import WorkflowContext
from social_media_auto_poster.graph.workflow import build_workflow, run_job
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, PublishResult
from social_media_auto_poster.platforms.simulator import Simulator
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
