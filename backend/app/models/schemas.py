"""Pydantic response models for the API. They mirror the core's records; file names only, no file contents."""

from datetime import datetime

from pydantic import BaseModel


class CandidateResponse(BaseModel):
    """A paired image + caption found by a scan, with what ``run`` would do with it."""

    stem: str
    media_name: str
    caption_name: str
    stable: bool
    unstable_files: list[str]  # "media" and/or "caption" while still being copied.
    valid: bool
    errors: list[str]
    duplicate_of: str | None  # Job ID that already handles this content, if any.


class ScanResponse(BaseModel):
    """Read-only report of the LinkedIn incoming folder."""

    platform: str
    candidates: list[CandidateResponse]
    pending: list[str]
    ambiguous: list[list[str]]
    interrupted_jobs: int  # PUBLISHING jobs the next run will recover.


class RecoveryResponse(BaseModel):
    """A job interrupted by an earlier crash, resolved at the start of this run."""

    job_id: str
    stem: str
    mode: str
    status: str
    notes: list[str]
    destination: str | None


class RunJobResponse(BaseModel):
    """One post's result in a run, as the ``run`` command prints it."""

    stem: str
    result: str  # e.g. "SIMULATED (not published)", "DUPLICATE", "FAILED".
    note: str
    details: list[str]
    outcome: str  # simulated, published, failed, needs_review, duplicates, waiting, or errors.
    problem: bool


class RunResponse(BaseModel):
    """Result of one run. Same exit codes as the ``run`` command."""

    exit_code: int
    simulated: bool  # Always true until Milestone 4: nothing is sent to LinkedIn.
    completed: bool  # False if the run stopped at setup; see the log.
    candidates: int
    recovered: list[RecoveryResponse]
    jobs: list[RunJobResponse]
    pending: list[str]
    ambiguous: list[list[str]]


class JobResponse(BaseModel):
    """A job record from the job database."""

    job_id: str
    platform: str
    mode: str  # "simulated" or "real".
    stem: str
    media_name: str
    caption_name: str
    media_sha256: str | None
    caption_sha256: str | None
    status: str
    platform_post_id: str | None
    destination: str | None
    errors: list[str]
    created_at: datetime
    updated_at: datetime
