# Social Media Auto-Poster

A local, folder-driven Python application scaffold for preparing and publishing social media content. The project is being built incrementally, starting with LinkedIn and adding other platforms only after their integrations are separately planned and validated.

## MVP goals

- Discover content placed in platform-specific incoming folders.
- Pair image files with UTF-8 caption files and validate them before processing.
- Track post state and processing history so interrupted work can be recovered safely.
- Publish through official platform APIs, archive only confirmed successes, and retain failures with useful diagnostics.
- Prevent duplicate processing and avoid blindly retrying requests whose publishing outcome is ambiguous.

## Non-goals for this scaffold

There is no working folder watcher, database/repository, scheduler, OAuth flow, or live publishing integration yet. The simulator does not contact a platform and its result is not confirmation of publication. This scaffold does not claim API access is configured.

## Current status

**Scaffold only (Milestone 1, Slices 0–5 complete).** What exists today:

- Packaging (`pyproject.toml`) with declared dependencies.
- An environment-variable template (`.env.example`).
- Shared models (`models.py`): `Platform`, `JobStatus` (including the terminal `SIMULATED` status), `PostCandidate`, `ScanResult`, `ValidationResult`, and `PublishResult`, which records a definite success or failure and always whether the result was simulated.
- Settings (`config.py`): resolves the content folder root and can create the `data/<Platform>/{incoming,archive,failed}` folders.
- A platform-neutral publisher protocol (`platforms/base.py`).
- A safe simulation publisher that never contacts any platform (`platforms/simulator.py`).
- Ingestion (`ingestion/`): a one-shot scan that pairs images with captions (`scanner.py`), a file-copy stability check (`stability.py`), and content validation (`validation.py`).
- A service that moves a post's two files together into `archive/` or `failed/` (`services/archive.py`). Nothing calls it yet.
- A read-only command-line `scan` (`main.py`); see "Scan the incoming folder" below.
- Unit tests under `tests/unit/`.

- A LangGraph workflow (`graph/`) that takes one post through stability → validation → simulated publish → record, moving invalid or failed posts to `failed/` with an `error-report.txt`. Simulated posts stay in `incoming/`. It is tested but not yet started by any command.

There is no storage (database) or `run` command yet.

Platform order:

1. LinkedIn — first integration milestone; not implemented.
2. Facebook — planned, not implemented.
3. Instagram — planned, not implemented.

Facebook and Instagram are placeholders for future work, not supported integrations.

## Project layout

Current layout, after the Milestone 1, Slice 1 migration. Packages marked "empty" contain only an `__init__.py` and get code in later slices:

```text
data/                              Local platform content; ignored by Git
  LinkedIn/{incoming,archive,failed}/
  Facebook/{incoming,archive,failed}/
  Instagram/{incoming,archive,failed}/
src/social_media_auto_poster/
  config.py                        Settings and data-folder creation
  models.py                        Platform, JobStatus, PostCandidate, ScanResult, ValidationResult, PublishResult
  platforms/                       Publisher protocol (base.py) and simulator (simulator.py)
  platforms/{linkedin,facebook,instagram}/   Empty
  graph/                           LangGraph workflow: state.py, nodes.py, workflow.py
  ingestion/                       Scanning/pairing, stability check, validation
  services/                        Moving posts to archive/failed (archive.py); hashing later
  storage/                         Empty; SQLite repository (Milestone 2)
tests/unit/                        Unit tests for every module above
scripts/, docs/                    Empty
logs/                              Local application logs; ignored by Git
```

`main.py` currently provides the read-only `scan` command; `run` arrives in Slice 6. The full target layout is in `CLAUDE.md` ("Target code layout").

## Content naming convention

The initial convention is one image and one UTF-8 text caption with the same filename stem, for example:

```text
data/LinkedIn/incoming/product-launch.jpg
data/LinkedIn/incoming/product-launch.txt
```

The planned initial image extensions are `.jpg`, `.jpeg`, and `.png`. Pairing, the file-copy stability check, and validation (extension, image signature, UTF-8 non-empty caption) exist; see `CLAUDE.md`, "Input conventions". This convention is provisional; media support will be reviewed against each platform's official API before implementation.

## Windows setup

Use Python 3.12 or newer; the project is developed on **Python 3.14**. From PowerShell in the project root:

```powershell
py -3.14 -m venv .vevn               # once
.\.vevn\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Dependencies are declared only in `pyproject.toml`. The `[dev]` extra adds `pytest`.

Verify the environment:

```powershell
python --version
python -m pip check
python -m pytest
```

## Scan the incoming folder (read-only)

The only command so far is `scan`. It reports what the tool would pick up and never moves or publishes anything:

```powershell
python -m social_media_auto_poster.main scan                          # data\LinkedIn\incoming (respects AUTOPOSTER_DATA_DIR)
python -m social_media_auto_poster.main scan --dir C:\some\folder     # any folder
python -m social_media_auto_poster.main scan --interval 0             # instant: checks locks, skips the change-watch wait
python -m social_media_auto_poster.main scan --help
```

It lists **candidates** (image + caption pairs, each marked stable or not, and valid or `INVALID:` with reasons), **pending** files waiting for a partner, and **ambiguous** stems that need fixing by hand. Exit code 0 means the scan completed; 1 means the folder does not exist. The `run` command (validate, simulate, record) arrives in Slice 6.

## Configuration and security

Copy `.env.example` to `.env` when local configuration is introduced. `.env` is ignored by Git; `.env.example` contains only blank placeholders. Do not commit credentials, access tokens, local databases, content, or logs. OAuth and API access are not configured by this scaffold. Future integrations must use official APIs and comply with their permissions and policies.

SQLite is planned for durable post state and restart recovery, but no database has been created. A publishing request with an unknown outcome must not be retried automatically without a platform-safe way to establish whether it succeeded. Content should be archived only after success is confirmed.

## Architecture decisions

- **LangGraph is required** for orchestrating each post's workflow as an explicit, deterministic state graph. It does not use LLMs or autonomous agents; filesystem, validation, database, and API work live in ordinary Python modules that the graph's nodes call.
- **SQLite first.** The application database (business records) and the LangGraph checkpoint store are kept logically separate and introduced in separate milestones. PostgreSQL is deferred until there is a concrete need.

See `CLAUDE.md` for the binding project rules.

## Development milestones

0. Inspect the scaffold, plan, and establish `CLAUDE.md` (done).
1. Local simulation: typed LangGraph state, initial deterministic graph, pairing and validation, simulated publishing, tests.
2. Durable application records: SQLite repository, job states, hashes, duplicate detection, restart reconciliation.
3. Persistent LangGraph execution: SQLite checkpointer, resume and interruption tests.
4. Real LinkedIn publishing through official APIs, with safeguards for ambiguous outcomes.
5. Reliability, operational docs, PostgreSQL evaluation, and Facebook/Instagram planning.

No live publishing, OAuth, or file-processing feature should be considered available until implemented and covered by appropriate tests.
