"""The per-post workflow graph. One graph run per discovered candidate.

Main path: check_stability -> validate -> check_duplicate -> mark_publishing -> publish -> record_result

Routes:
- not stable / unreadable: check_stability or check_duplicate -> record_result -> END (no DB row)
- invalid:                 ... validate -> move_to_failed -> record_result -> END
- duplicate:               ... check_duplicate (or mark_publishing) -> record_result -> END (no DB row)
- FAILED/NEEDS_REVIEW:     ... publish -> move_to_failed -> record_result -> END
- SIMULATED:               ... publish -> record_result -> END  (files stay in incoming/)
- real PUBLISHED:          ... publish -> record_result -> archive -> END

The job row is committed as PUBLISHING in mark_publishing, before publish.
Only a confirmed real ``PUBLISHED`` result reaches ``archive``, and only after
``record_result`` has recorded it.

Checkpoints (Milestone 3): with a checkpointer, every step's state is saved with
``durability="sync"``, i.e. on disk *before* the next step starts, so the
checkpoint that says "publish is next" exists before publish is called. A
finished job's thread is deleted: its business record lives in the job database.
"""

import logging
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.state import JobState, WorkflowContext, new_job_state
from social_media_auto_poster.models import PostCandidate

logger = logging.getLogger(__name__)

# Every graph run, first or resumed, saves each step before starting the next.
DURABILITY = "sync"


def build_workflow(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """Build and compile the graph, saving checkpoints to ``checkpointer`` if given."""
    graph = StateGraph(JobState, context_schema=WorkflowContext)

    graph.add_node(nodes.CHECK_STABILITY, nodes.check_stability)
    graph.add_node(nodes.VALIDATE, nodes.validate)
    graph.add_node(nodes.CHECK_DUPLICATE, nodes.check_duplicate)
    graph.add_node(nodes.MARK_PUBLISHING, nodes.mark_publishing)
    graph.add_node(nodes.PUBLISH, nodes.publish)
    graph.add_node(nodes.MOVE_TO_FAILED, nodes.move_to_failed)
    graph.add_node(nodes.RECORD_RESULT, nodes.record_result)
    graph.add_node(nodes.ARCHIVE, nodes.archive)

    graph.add_edge(START, nodes.CHECK_STABILITY)
    graph.add_conditional_edges(
        nodes.CHECK_STABILITY,
        nodes.route_after_stability,
        {"stable": nodes.VALIDATE, "waiting": nodes.RECORD_RESULT},
    )
    graph.add_conditional_edges(
        nodes.VALIDATE,
        nodes.route_after_validate,
        {"valid": nodes.CHECK_DUPLICATE, "invalid": nodes.MOVE_TO_FAILED},
    )
    graph.add_conditional_edges(
        nodes.CHECK_DUPLICATE,
        nodes.route_after_duplicate_check,
        {
            "new": nodes.MARK_PUBLISHING,
            "duplicate": nodes.RECORD_RESULT,
            "waiting": nodes.RECORD_RESULT,
        },
    )
    graph.add_conditional_edges(
        nodes.MARK_PUBLISHING,
        nodes.route_after_mark_publishing,
        {"new": nodes.PUBLISH, "duplicate": nodes.RECORD_RESULT},
    )
    graph.add_conditional_edges(
        nodes.PUBLISH,
        nodes.route_after_publish,
        {"failed": nodes.MOVE_TO_FAILED, "recorded": nodes.RECORD_RESULT},
    )
    graph.add_edge(nodes.MOVE_TO_FAILED, nodes.RECORD_RESULT)
    graph.add_conditional_edges(
        nodes.RECORD_RESULT,
        nodes.route_after_record,
        {"published": nodes.ARCHIVE, "end": END},
    )
    graph.add_edge(nodes.ARCHIVE, END)

    return graph.compile(checkpointer=checkpointer)


def run_job(candidate: PostCandidate, context: WorkflowContext) -> JobState:
    """Run one candidate through the graph and return its final state.

    The job ID is a random UUID. It becomes the database job ID if the job is
    recorded (duplicates and files still being copied are never recorded), and
    it is the graph thread ID in the checkpoint store. If the run ends normally,
    the thread is deleted; if it raises (or the process dies), the thread stays
    for startup resume to find.
    """
    job_id = uuid4().hex
    final = build_workflow(context.checkpointer).invoke(
        new_job_state(job_id, candidate, context.mode),
        config={"configurable": {"thread_id": job_id}},
        context=context,
        **durability_for(context.checkpointer),
    )
    forget_thread(context.checkpointer, job_id)
    return final


def durability_for(checkpointer: BaseCheckpointSaver | None) -> dict[str, str]:
    """``durability="sync"`` when checkpoints are saved; nothing otherwise.

    Durability only means something with a checkpointer, and langgraph 1.2.18
    raises AttributeError for ``durability="sync"`` on a graph without one.
    """
    return {"durability": DURABILITY} if checkpointer is not None else {}


def forget_thread(checkpointer: BaseCheckpointSaver | None, thread_id: str) -> None:
    """Delete a finished job's checkpoints. A failure is logged, never raised: the job itself is done."""
    if checkpointer is None:
        return
    try:
        checkpointer.delete_thread(thread_id)
    except Exception:  # Deliberately broad: a leftover thread is only cleaned up by the next run.
        # No ID: a skipped job's thread ID is never stored, and only stored job IDs are logged.
        logger.exception("Could not delete a finished checkpoint thread; the next run deletes it")