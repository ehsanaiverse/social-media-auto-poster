"""Startup resume of graph threads left behind by a crash (Milestone 3).

``run`` calls ``resume_interrupted_jobs`` after taking the single-run lock and
opening both stores, and before reconciling PUBLISHING rows. Finished jobs have
their thread deleted, so every thread still saved belongs to a job that stopped
part-way. What happens depends on the step the checkpoint says is next:

=================================================  =================================================
Next step                                          Action
=================================================  =================================================
check_stability, validate, check_duplicate,        Not resumed: publish never started (checkpoints
mark_publishing                                    are saved before each step). A PUBLISHING row is
                                                   settled as FAILED ("nothing was sent"), in either
                                                   mode; the post is processed again by this run.
publish                                            Never resumed: the outcome is unknown. Left to
                                                   reconciliation (simulated: FAILED; real:
                                                   NEEDS_REVIEW).
record_result, move_to_failed, archive             Resumed: publish already returned and its result
                                                   is in the checkpoint. These steps are safe to
                                                   run again.
anything else, an unreadable checkpoint, or no    Not resumed (warning logged); left to
next step (a finished job's thread is normally     reconciliation.
deleted; LangGraph also reports no next step for
a node that no longer exists)
=================================================  =================================================

Every thread is deleted afterwards, except one whose resume raised: it stays for
the next run, and reconciliation skips its job.
"""

import logging
from dataclasses import dataclass, field

from langgraph.checkpoint.base import BaseCheckpointSaver

from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.checkpoints import saved_thread_ids
from social_media_auto_poster.graph.state import JobState, WorkflowContext
from social_media_auto_poster.graph.workflow import build_workflow, durability_for, forget_thread
from social_media_auto_poster.models import JobStatus, PostCandidate, PublishMode, PublishResult
from social_media_auto_poster.services.reconcile import Recovery, settle_crashed_before_publish
from social_media_auto_poster.storage.jobs import JobRepository

logger = logging.getLogger(__name__)

BEFORE_PUBLISH = frozenset({nodes.CHECK_STABILITY, nodes.VALIDATE, nodes.CHECK_DUPLICATE, nodes.MARK_PUBLISHING})
RESUMABLE = frozenset({nodes.RECORD_RESULT, nodes.MOVE_TO_FAILED, nodes.ARCHIVE})


class _NeverPublish:
    """The publisher for resumed jobs. Resumed steps never reach publish; if one did, it fails loudly."""

    def publish(self, post: PostCandidate, /) -> PublishResult:
        raise RuntimeError("A resumed job must never publish again.")


@dataclass
class ResumeResult:
    resumed: list[tuple[JobState, WorkflowContext]] = field(default_factory=list)  # Finished by this resume.
    recoveries: list[Recovery] = field(default_factory=list)  # PUBLISHING rows settled as FAILED.
    failed: list[tuple[str, str]] = field(default_factory=list)  # (job_id, stem) whose resume raised.

    @property
    def skip_reconcile(self) -> frozenset[str]:
        return frozenset(job_id for job_id, _ in self.failed)


def resume_interrupted_jobs(
    checkpointer: BaseCheckpointSaver,
    repository: JobRepository,
    settings: Settings,
) -> ResumeResult:
    """Handle every saved thread as the table above says."""
    result = ResumeResult()
    graph = build_workflow(checkpointer)
    for thread_id in saved_thread_ids(checkpointer):
        state, next_step = _read(graph, thread_id, repository)
        if state is None:
            pass  # Unknown or unreadable: already logged; reconciliation decides.
        elif next_step in BEFORE_PUBLISH:
            record = repository.get(thread_id)
            if record is not None and record.status is JobStatus.PUBLISHING:
                result.recoveries.append(settle_crashed_before_publish(record, repository, settings))
        elif next_step == nodes.PUBLISH:
            pass  # Outcome unknown: never resumed. Reconciliation decides.
        else:  # RESUMABLE
            context = _context(state, repository, settings, checkpointer)
            if not _resume(graph, thread_id, state, next_step, context, result):
                continue  # Keep the thread for the next run.
        forget_thread(checkpointer, thread_id)
    return result


def _read(graph, thread_id: str, repository: JobRepository) -> tuple[JobState | None, str | None]:
    """The saved state and its single, known next step, or (None, None) if the checkpoint is unusable."""
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        values, next_steps = snapshot.values, tuple(snapshot.next)
    except Exception as error:  # Deliberately broad: corrupt data, a newer format, a bug in an old checkpoint.
        _warn_unusable(thread_id, repository, f"unreadable ({type(error).__name__}: {error})")
        return None, None

    problem = _problem(values, next_steps, thread_id)
    if problem is not None:
        _warn_unusable(thread_id, repository, problem)
        return None, None
    return values, next_steps[0]


def _problem(values, next_steps: tuple, thread_id: str) -> str | None:
    if not values:
        return "empty checkpoint"
    if not next_steps:
        return "no next step (a finished job whose thread was not deleted, or a node that no longer exists)"
    missing = sorted(set(JobState.__annotations__) - set(values or {}))
    if missing:
        return f"saved state is missing {', '.join(missing)} (older or newer format)"
    if values["job_id"] != thread_id:
        return "saved job ID does not match the thread"
    if values["mode"] not in {mode.value for mode in PublishMode}:
        return f"unknown mode {values['mode']!r}"
    if len(next_steps) > 1:
        return f"more than one next step {list(next_steps)}"
    if next_steps[0] not in BEFORE_PUBLISH | {nodes.PUBLISH} | RESUMABLE:
        return f"unknown next step {next_steps[0]!r} (renamed or removed node)"
    return None


def _warn_unusable(thread_id: str, repository: JobRepository, problem: str) -> None:
    # Only a stored job's ID is ever logged.
    stored = repository.get(thread_id) is not None
    logger.warning(
        "Checkpoint not resumed: %s problem=%s; reconciliation handles the job",
        f"job_id={thread_id}" if stored else "job not stored",
        problem,
    )


def _context(
    state: JobState, repository: JobRepository, settings: Settings, checkpointer: BaseCheckpointSaver
) -> WorkflowContext:
    return WorkflowContext(
        publisher=_NeverPublish(),
        mode=PublishMode(state["mode"]),  # The job's own mode, whatever this run's mode is.
        repository=repository,
        settings=settings,
        stability_interval_s=0,
        checkpointer=checkpointer,
    )


def _resume(graph, thread_id: str, state: JobState, next_step: str, context: WorkflowContext, result) -> bool:
    skipped = state["duplicate_of"] or state["retry_later"]
    who = f"job not stored stem={state['stem']}" if skipped else f"job_id={thread_id} stem={state['stem']}"
    logger.warning(
        "Resuming interrupted job: %s platform=%s mode=%s status=%s next=%s",
        who, state["platform"], state["mode"], state["status"], next_step,
    )
    try:
        final = graph.invoke(
            None,
            config={"configurable": {"thread_id": thread_id}},
            context=context,
            **durability_for(context.checkpointer),
        )
    except Exception:  # A bug or a database failure. Keep the thread; try again next run.
        logger.exception("Resume failed; kept for the next run: %s", who)
        result.failed.append((thread_id, state["stem"]))
        return False
    result.resumed.append((final, context))
    return True
