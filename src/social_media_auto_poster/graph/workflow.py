"""The per-post workflow graph. One graph run per discovered candidate.

Routes:
- not stable yet:     check_stability -> record_result -> END
- invalid:            ... validate -> move_to_failed -> record_result -> END
- FAILED/NEEDS_REVIEW: ... publish -> move_to_failed -> record_result -> END
- SIMULATED:          ... publish -> record_result -> END  (files stay in incoming/)
- real PUBLISHED:     ... publish -> record_result -> archive -> END

Only a confirmed real ``PUBLISHED`` result reaches ``archive``, and only after
``record_result`` has recorded it.
"""

from functools import cache
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.state import JobState, WorkflowContext, new_job_state
from social_media_auto_poster.models import PostCandidate


@cache
def build_workflow() -> CompiledStateGraph:
    """Build and compile the graph once; the compiled graph is reusable."""
    graph = StateGraph(JobState, context_schema=WorkflowContext)

    graph.add_node(nodes.CHECK_STABILITY, nodes.check_stability)
    graph.add_node(nodes.VALIDATE, nodes.validate)
    graph.add_node(nodes.MARK_PUBLISHING, nodes.mark_publishing)
    graph.add_node(nodes.PUBLISH, nodes.publish)
    graph.add_node(nodes.MOVE_TO_FAILED, nodes.move_to_failed)
    graph.add_node(nodes.RECORD_RESULT, nodes.record_result)
    graph.add_node(nodes.ARCHIVE, nodes.archive)

    graph.add_edge(START, nodes.CHECK_STABILITY)
    graph.add_conditional_edges(
        nodes.CHECK_STABILITY,
        nodes.route_after_stability,
        {nodes.VALIDATE: nodes.VALIDATE, nodes.RECORD_RESULT: nodes.RECORD_RESULT},
    )
    graph.add_conditional_edges(
        nodes.VALIDATE,
        nodes.route_after_validate,
        {nodes.MARK_PUBLISHING: nodes.MARK_PUBLISHING, nodes.MOVE_TO_FAILED: nodes.MOVE_TO_FAILED},
    )
    graph.add_edge(nodes.MARK_PUBLISHING, nodes.PUBLISH)
    graph.add_conditional_edges(
        nodes.PUBLISH,
        nodes.route_after_publish,
        {nodes.MOVE_TO_FAILED: nodes.MOVE_TO_FAILED, nodes.RECORD_RESULT: nodes.RECORD_RESULT},
    )
    graph.add_edge(nodes.MOVE_TO_FAILED, nodes.RECORD_RESULT)
    graph.add_conditional_edges(
        nodes.RECORD_RESULT,
        nodes.route_after_record,
        {nodes.ARCHIVE: nodes.ARCHIVE, "end": END},
    )
    graph.add_edge(nodes.ARCHIVE, END)

    return graph.compile()


def run_job(candidate: PostCandidate, context: WorkflowContext) -> JobState:
    """Run one candidate through the graph and return its final state.

    The job ID is a random UUID in Milestone 1; Milestone 2 replaces it with
    the database job ID. It is also the graph thread ID, which matters once a
    checkpointer is added in Milestone 3.
    """
    job_id = uuid4().hex
    return build_workflow().invoke(
        new_job_state(job_id, candidate),
        config={"configurable": {"thread_id": job_id}},
        context=context,
    )