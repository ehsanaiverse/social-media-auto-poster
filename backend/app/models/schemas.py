"""Data models and Pydantic schemas for the API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CandidateResponse(BaseModel):
    """A paired image + caption candidate from a scan."""

    stem: str
    media_path: str
    caption_path: str


class ScanResponse(BaseModel):
    """Result of scanning an incoming folder."""

    platform: str
    candidates: list[CandidateResponse] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    ambiguous: list[list[str]] = Field(default_factory=list)


class JobOutcome(BaseModel):
    """Result of running one candidate through the graph."""

    stem: str
    status: str
    errors: list[str] = Field(default_factory=list)
    simulated: bool
    platform_post_id: Optional[str] = None
    destination: Optional[str] = None
    retry_later: bool = False


class RunResponse(BaseModel):
    """Result of processing all candidates in a run."""

    platform: str
    candidates: int = 0
    pending: list[str] = Field(default_factory=list)
    ambiguous: list[list[str]] = Field(default_factory=list)
    jobs: list[JobOutcome] = Field(default_factory=list)
    error: Optional[str] = None


class JobResponse(BaseModel):
    """A job record from the database."""

    id: int
    platform: str
    stem: str
    media_path: str
    caption_path: str
    media_hash: Optional[str] = None
    caption_hash: Optional[str] = None
    status: str
    error: Optional[str] = None
    platform_post_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class JobListResponse(BaseModel):
    """A list of job records."""

    jobs: list[JobResponse]
    total: int