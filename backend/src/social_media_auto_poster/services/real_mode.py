"""Safeguards checked before a real run publishes anything (D33, Milestone 4).

Both are plain checks; ``run --real`` decides what to do with the answers.

- ``posts_only_simulated``: simulated posts stay in incoming/, and duplicates are
  checked per mode (D20), so a first real run would post every one of them.
- ``archive_without_history``: the job database is the only protection against
  posting the same content twice. Published posts in archive/ while the database
  has no real jobs at all means it was probably replaced or lost.
"""

from dataclasses import dataclass
from pathlib import Path

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository


@dataclass(frozen=True)
class SimulatedOnly:
    """A post whose exact content was simulated but never handled in real mode."""

    candidate: PostCandidate
    simulated_job: JobRecord


def posts_only_simulated(candidates: tuple[PostCandidate, ...], repository: JobRepository) -> list[SimulatedOnly]:
    """Candidates that a real run would post for the first time although they were already simulated.

    Content that already has a live real job is left out: a real run skips it as a
    duplicate anyway. Files that cannot be read right now are left out too: the
    graph waits for them.
    """
    found = []
    for candidate in candidates:
        try:
            content = {
                "platform": candidate.platform,
                "media_sha256": sha256_file(candidate.media_path),
                "caption_sha256": sha256_file(candidate.caption_path),
            }
        except OSError:
            continue
        if repository.find_blocking_duplicate(mode=PublishMode.REAL, **content) is not None:
            continue
        simulated = repository.find_blocking_duplicate(mode=PublishMode.SIMULATED, **content)
        if simulated is not None and simulated.status is JobStatus.SIMULATED:
            found.append(SimulatedOnly(candidate, simulated))
    return found


def archive_without_history(settings: Settings, repository: JobRepository | None, platform: Platform) -> list[Path]:
    """Post folders in archive/ when the job database has no real jobs (or does not exist yet).

    Only a confirmed real publish is archived, so any folder there means real
    posts were made that this database does not know about.
    """
    if repository is not None and repository.has_jobs(mode=PublishMode.REAL):
        return []
    archive = settings.archive_dir(platform)
    if not archive.is_dir():
        return []
    return sorted(path for path in archive.iterdir() if path.is_dir())
