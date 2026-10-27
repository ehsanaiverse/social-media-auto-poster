"""The LangGraph checkpoint store is a separate SQLite file from the application database."""

import sqlite3

import pytest
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph.checkpoints import open_checkpointer, saved_thread_ids
from social_media_auto_poster.storage.database import open_app_db


class Counter(TypedDict):
    n: int


def tiny_graph(saver):
    graph = StateGraph(Counter)
    graph.add_node("add", lambda state: {"n": state["n"] + 1})
    graph.add_edge(START, "add")
    graph.add_edge("add", END)
    return graph.compile(checkpointer=saver)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_dir=tmp_path, data_dir=tmp_path / "data")


def tables(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_checkpoint_store_is_its_own_file_next_to_the_app_database(settings):
    assert settings.checkpoint_db_path == settings.base_dir / "database" / "langgraph_checkpoints.db"
    assert settings.checkpoint_db_path != settings.app_db_path

    open_app_db(settings.app_db_path).close()
    with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path):
        pass

    assert {"checkpoints", "writes"} <= tables(settings.checkpoint_db_path)
    assert "jobs" not in tables(settings.checkpoint_db_path)
    assert tables(settings.app_db_path).isdisjoint({"checkpoints", "writes"})


def test_refuses_the_application_database_path(settings):
    with pytest.raises(ValueError, match="must not be the application database"):
        with open_checkpointer(settings.app_db_path, app_db_path=settings.app_db_path):
            pass
    assert not settings.app_db_path.exists()


def test_writes_are_durable_before_the_next_step(settings):
    with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
        assert saver.conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert saver.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_lists_saved_threads_and_survives_reopening(settings):
    paths = dict(path=settings.checkpoint_db_path, app_db_path=settings.app_db_path)
    with open_checkpointer(**paths) as saver:
        assert saved_thread_ids(saver) == []
        graph = tiny_graph(saver)
        for thread in ("job-a", "job-b"):
            graph.invoke({"n": 0}, {"configurable": {"thread_id": thread}}, durability="sync")

    with open_checkpointer(**paths) as saver:
        assert saved_thread_ids(saver) == ["job-a", "job-b"]
        saver.delete_thread("job-a")
        assert saved_thread_ids(saver) == ["job-b"]


def test_one_unreadable_checkpoint_does_not_hide_the_others(settings):
    paths = dict(path=settings.checkpoint_db_path, app_db_path=settings.app_db_path)
    with open_checkpointer(**paths) as saver:
        graph = tiny_graph(saver)
        for thread in ("job-a", "job-b"):
            graph.invoke({"n": 0}, {"configurable": {"thread_id": thread}}, durability="sync")
        saver.conn.execute("UPDATE checkpoints SET checkpoint = X'00FF' WHERE thread_id = 'job-a'")
        saver.conn.commit()

        assert saved_thread_ids(saver) == ["job-a", "job-b"]
