"""Find posts in an ``incoming/`` folder by pairing media files with captions.

The scanner only lists and pairs files. It never moves, opens, hashes, or
validates them: extension checks belong to validation, and copy-completeness
checks belong to ``stability``.
"""

import logging
from collections import defaultdict
from pathlib import Path

from social_media_auto_poster.models import Platform, PostCandidate, ScanResult

logger = logging.getLogger(__name__)

CAPTION_SUFFIX = ".txt"

# Files that are never part of a post: OS metadata, editor lock files, and
# partial downloads. They are skipped entirely, not reported as pending.
IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini"})
IGNORED_PREFIXES = (".", "~$")
IGNORED_SUFFIXES = frozenset({".tmp", ".part", ".crdownload"})


def scan_incoming(folder: Path, platform: Platform) -> ScanResult:
    """Pair media files with same-stem ``.txt`` captions in ``folder``.

    Matching is case-insensitive, as it is for Windows users. Each stem falls
    into exactly one bucket:

    - one media file and one caption: a candidate;
    - more than one media file or caption: ambiguous (left untouched);
    - otherwise: pending, waiting for its partner (left untouched).

    Any non-``.txt`` file counts as media here, so an unsupported image type
    still reaches validation and fails there instead of waiting forever.

    Raises ``FileNotFoundError`` if ``folder`` does not exist, because a
    missing folder is a configuration error, not an empty inbox.
    """
    if not folder.is_dir():
        raise FileNotFoundError(f"Incoming folder does not exist: {folder}")

    groups: dict[str, list[Path]] = defaultdict(list)
    for path in folder.iterdir():
        if path.is_file() and not _is_ignored(path):
            groups[path.stem.casefold()].append(path)

    candidates: list[PostCandidate] = []
    pending: list[Path] = []
    ambiguous: list[tuple[Path, ...]] = []

    for key in sorted(groups):
        files = sorted(groups[key], key=_name_key)
        captions = [p for p in files if p.suffix.casefold() == CAPTION_SUFFIX]
        media = [p for p in files if p.suffix.casefold() != CAPTION_SUFFIX]

        if len(media) > 1 or len(captions) > 1:
            ambiguous.append(tuple(files))
            logger.warning(
                "Ambiguous post files left untouched: platform=%s stem=%s files=%s",
                platform,
                media[0].stem if media else captions[0].stem,
                [p.name for p in files],
            )
        elif media and captions:
            candidates.append(
                PostCandidate(
                    platform=platform,
                    stem=media[0].stem,
                    media_path=media[0],
                    caption_path=captions[0],
                )
            )
        else:
            pending.extend(files)
            logger.debug(
                "Pending file waiting for its partner: platform=%s file=%s",
                platform,
                files[0].name,
            )

    return ScanResult(
        candidates=tuple(candidates),
        pending=tuple(sorted(pending, key=_name_key)),
        ambiguous=tuple(ambiguous),
    )


def _is_ignored(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in IGNORED_NAMES
        or name.startswith(IGNORED_PREFIXES)
        or path.suffix.casefold() in IGNORED_SUFFIXES
    )


def _name_key(path: Path) -> tuple[str, str]:
    return (path.name.casefold(), path.name)
