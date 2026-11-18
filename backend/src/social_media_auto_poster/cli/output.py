"""How results are printed in the terminal. Everything shown comes from the operations' results."""

import sys
from zoneinfo import ZoneInfo

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, PublishMode, ScanResult
from social_media_auto_poster.operations.common import DUPLICATE_LABEL, _describe_original, _local_time, _minutes
from social_media_auto_poster.operations.run import JobLine, RunReport, ScheduledPost
from social_media_auto_poster.operations.scan import CandidateView, ScheduleView
from social_media_auto_poster.services.reconcile import Recovery
from social_media_auto_poster.services.scheduling import CONTENT_LABELS, CONTENT_MISSING
from social_media_auto_poster.storage.jobs import JobRecord


def _print_candidates(views: tuple[CandidateView, ...], mode: PublishMode, zone: ZoneInfo) -> None:
    if not views:
        return
    pairs = [f"{v.candidate.media_path.name} + {v.candidate.caption_path.name}" for v in views]
    stability = [_stability_label(v) for v in views]
    stem_width = max(len(v.candidate.stem) for v in views)
    pair_width = max(len(p) for p in pairs)
    stability_width = max(len(s) for s in stability)
    print(f"\nCANDIDATES ({len(views)})")
    for view, pair, stable in zip(views, pairs, stability):
        if view.schedule is not None:  # Runs skip it until it is due; say if it will not be published.
            state = "valid" if view.valid else f"INVALID: {'; '.join(view.errors)}"
            last = f"{state}   SCHEDULED for {_local_time(view.schedule.publish_at, zone)} ({view.schedule.mode})"
            if view.schedule_content in CONTENT_LABELS:
                last += f" – {CONTENT_LABELS[view.schedule_content]}"
        elif not view.valid:
            last = f"INVALID: {'; '.join(view.errors)}"
        elif view.stable:
            last = f"valid   {_duplicate_label(view, mode)}"
        else:
            last = "valid"  # Not hashed while it may still be changing.
        print(f"  {view.candidate.stem:<{stem_width}}   {pair:<{pair_width}}   {stable:<{stability_width}}   {last}")


def _print_missing_schedules(views: tuple[ScheduleView, ...], zone: ZoneInfo) -> None:
    width = max(len(v.schedule.stem) for v in views)
    print(f"\nSCHEDULED, FILES MISSING ({len(views)}) - these will not be published; re-confirm or unschedule them")
    for view in views:
        s = view.schedule
        label = CONTENT_LABELS.get(view.content or "", CONTENT_LABELS[CONTENT_MISSING])
        print(f"  {s.stem:<{width}}   {_local_time(s.publish_at, zone)} ({s.mode})   {label}")


def _duplicate_label(view: CandidateView, mode: PublishMode) -> str:
    """Whether ``run`` (in ``mode``) would skip this post as a duplicate. Same check as the graph, read-only."""
    if view.duplicate is not None:
        return f"DUPLICATE {_describe_original(view.duplicate)}"
    if view.duplicate_check_failed:
        return "could not read files to check for duplicates"
    if view.simulated is not None:
        return (f"new, but already simulated as job {view.simulated.job_id[:8]} "
                "(run --real refuses it without --publish-simulated)")
    return "new"


def _stability_label(view: CandidateView) -> str:
    if view.stable:
        return "stable"
    return f"NOT STABLE ({' and '.join(view.unstable)} changing or locked)"


def _print_job_table(records: list[JobRecord]) -> None:
    stem_width = max(20, *(len(r.stem) for r in records))
    print(f"  {'JOB':<9} {'CREATED (UTC)':<20} {'STEM':<{stem_width}} {'MODE':<10} STATUS")
    for r in records:
        print(f"  {r.job_id[:8]:<9} {r.created_at:%Y-%m-%d %H:%M:%S}  {r.stem:<{stem_width}} {r.mode:<10} {r.status}")
        for error in r.errors:
            print(f"  {'':<9} - {error}")


def _print_resolve_next_steps(job: JobRecord, status: JobStatus, settings: Settings) -> None:
    files = f"{job.media_name} and {job.caption_name}"
    incoming = settings.incoming_dir(job.platform)
    if job.destination:
        print(f"Files: {files} are in {job.destination}")
    else:
        print(f"Files: {files} were not moved by the app (check {incoming})")

    if status is JobStatus.PUBLISHED:
        print("Next: the content stays blocked from re-posting. Keep or delete the files as you like.")
    elif job.destination:
        print(f"Next: to retry, fix the post if needed, move {files} back into {incoming}, then run again.")
    else:
        print("Next: if the files are still in incoming/, the next run will process them again.")


def _print_schedule_table(views: tuple[ScheduleView, ...], zone: ZoneInfo) -> None:
    stem_width = max(16, *(len(v.schedule.stem) for v in views))
    when = f"WHEN ({zone.key})"
    print(f"  {'ID':<9} {'POST':<{stem_width}} {'MODE':<10} {when:<22} {'UTC':<17} STATUS")
    for view in views:
        r = view.schedule
        local = f"{r.publish_at.astimezone(zone):%Y-%m-%d %H:%M}"
        print(f"  {r.schedule_id[:8]:<9} {r.stem:<{stem_width}} {r.mode:<10} {local:<22} "
              f"{r.publish_at:%Y-%m-%d %H:%M}  {r.status}")
        if view.content in CONTENT_LABELS:  # Found by reading the files now; the row itself is unchanged.
            print(f"  {'':<9} - {CONTENT_LABELS[view.content]}")
        if r.job_id:
            print(f"  {'':<9} - job {r.job_id[:8]}")
        if r.reason:
            print(f"  {'':<9} - {r.reason}")


def _print_run_report(report: RunReport, settings: Settings) -> None:
    """The terminal view of a run. Everything it shows is already in ``report``."""
    if report.resumed:
        _print_jobs(list(report.resumed), title="RESUMED", note="jobs finished after an earlier crash")
    if report.recoveries:
        _print_recoveries(list(report.recoveries))
    if report.scheduled:
        _print_scheduled(report.scheduled)
    if report.refusal is not None:
        if report.refusal.text:
            stream = sys.stdout if report.refusal.kind == "simulated_only" else sys.stderr
            print(report.refusal.text, file=stream)
        return
    if report.scan is None:  # Setup error: the log says what happened.
        return
    if report.scheduled_run:
        _print_scheduled_run(report)
    if report.jobs:
        _print_jobs(list(report.jobs))
    elif report.scheduled_run:
        if not report.missed:
            print("\nNo scheduled post is due.")
    else:
        print("\nNothing else to process." if report.scheduled else "\nNothing to process.")
    _print_pending_and_ambiguous(report.scan)
    print(report.footer)
    if any(line.label == DUPLICATE_LABEL for line in report.jobs):
        print("Duplicates stay in incoming/; delete them when you no longer need them.")
    print(f"Log: {settings.log_file}")


def _print_scheduled_run(report: RunReport) -> None:
    def late(post: ScheduledPost) -> str:
        minutes = _minutes(post.late_by) if post.late_by is not None else 0
        return "on time" if minutes <= 0 else f"{minutes} min late"

    for title, note, posts in (
        ("DUE", "carried out by this run (one per run)", report.due),
        ("MISSED", "more than 6 hours late; not published, nothing sent (schedule them again)", report.missed),
        ("WAITING", "also due; one post per run, so they go in the next scheduled runs", report.waiting),
    ):
        if posts:
            width = max(len(p.stem) for p in posts)
            print(f"\n{title} ({len(posts)}) - {note}")
            for post in posts:
                print(f"  {post.stem:<{width}}   {post.publish_at_local} ({post.mode}), {late(post)}")


def _print_scheduled(posts: tuple[ScheduledPost, ...]) -> None:
    width = max(len(post.stem) for post in posts)
    print(f"\nSCHEDULED ({len(posts)}) - left in incoming/ for their scheduled time")
    for post in posts:
        print(f"  {post.stem:<{width}}   {post.publish_at_local} ({post.mode})")


def _print_recoveries(recoveries: list[Recovery]) -> None:
    stem_width = max(len(r.stem) for r in recoveries)
    status_width = max(len(r.status) for r in recoveries)
    print(f"\nRECOVERED ({len(recoveries)}) - jobs interrupted by an earlier crash")
    for r in recoveries:
        first, *rest = r.notes
        print(f"  {r.stem:<{stem_width}}   {r.status:<{status_width}}   ({r.mode}) {first}")
        for note in rest:
            print(f"  {'':<{stem_width}}   {'':<{status_width}}   - {note}")


def _print_jobs(lines: list[JobLine], *, title: str = "JOBS", note: str = "") -> None:
    stem_width = max(len(line.stem) for line in lines)
    label_width = max(len(line.label) for line in lines)
    print(f"\n{title} ({len(lines)})" + (f" - {note}" if note else ""))
    for line in lines:
        print(f"  {line.stem:<{stem_width}}   {line.label:<{label_width}}   {line.note}")
        for detail in line.details:
            print(f"  {'':<{stem_width}}     - {detail}")


def _print_pending_and_ambiguous(result: ScanResult) -> None:
    if result.pending:
        print(f"\nPENDING ({len(result.pending)}) - waiting for a partner file")
        for path in result.pending:
            print(f"  {path.name}")

    if result.ambiguous:
        print(f"\nAMBIGUOUS ({len(result.ambiguous)}) - fix by hand, more than one file per stem")
        for group in result.ambiguous:
            print(f"  {group[0].stem}: {', '.join(p.name for p in group)}")
