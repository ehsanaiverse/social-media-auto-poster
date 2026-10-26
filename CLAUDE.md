# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.
These rules are binding. If `README.md` or other docs disagree with this file, this file wins and the other doc should be corrected.

## Project summary

A Windows-first, folder-driven social media auto-poster. A user drops an image and a matching caption file into a platform's `incoming/` folder; the application detects, validates, tracks, and (eventually) publishes the post through official platform APIs.

- **LinkedIn** is the only platform in scope. Facebook and Instagram have folders only; do not implement them.
- **Publishing is simulated** until Milestone 4. No code may contact LinkedIn (or any platform) before then.
- The developer has intermediate Python/AI experience: favour readable code, explain design decisions, and build incrementally.

## Mandatory architectural decisions

### 1. LangGraph orchestrates the workflow (required)

LangGraph is a **required** part of this project, not an optional or future enhancement. Do not propose replacing it with a hand-written loop or postponing it.

- The per-post workflow is an explicit, deterministic `StateGraph`: typed state, named nodes, explicit edges, and conditional edges where routing depends on results.
- **Discovery and pairing run in `ingestion/`, outside the graph.** Each paired post becomes one graph invocation (one graph thread per job). The graph starts at stability/validation, not discovery.
- Planned graph stages (implemented incrementally, not all at once): stability/validation → duplicate check → prepare → publish (simulated first) → record result → archive on confirmed `PUBLISHED`; plus an error/`NEEDS_REVIEW` path that preserves the files.
- **Implemented graph (Slice 5, `graph/workflow.py`):** `check_stability` → `validate` → `mark_publishing` → `publish` → `record_result` → (`archive` only on a real `PUBLISHED`) → END. Not stable → `record_result` (status stays `DISCOVERED`, `retry_later=True`). Invalid, `FAILED`, or `NEEDS_REVIEW` → `move_to_failed` → `record_result` → END. **Every path passes `record_result`, and a real publish is recorded before any file move** (in M2 `record_result` writes the DB). `duplicate_check` (M2) and `prepare` (M4) are not added until they have real work.
- `mark_publishing` is a separate node so that, with checkpoints (M3), `PUBLISHING` is saved before the publish call. Any exception from `publisher.publish` → `NEEDS_REVIEW`, never retried. If archiving fails after `PUBLISHED`, the status stays `PUBLISHED` and the error is recorded. If writing `error-report.txt` fails, it is logged and the outcome is unchanged.
- State is `graph/state.py: JobState` (a `TypedDict` of plain values; `errors` is a list). Live dependencies (publisher, settings, stability interval) go in `WorkflowContext`, passed with `invoke(..., context=...)` (`context_schema`; verified on langgraph 1.2.17). The job ID is a random UUID, also used as the graph `thread_id`; from M2 it is also the primary key of the `jobs` table (decision D18).
- **LangSmith tracing must stay off** (`LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` unset or false): it would send state (paths, captions) to an external service. A test enforces this.
- Nodes are thin. Filesystem work, media validation, hashing, database access, and API calls live in ordinary Python service modules; nodes call them and return state updates.
- **No LLMs and no autonomous agents.** Do not add `langchain` model integrations, tool-calling agents, or prompts unless a concrete, agreed requirement justifies it.
- Keep graph state small and serializable: IDs, paths (as strings), statuses, metadata, error details. Never put image bytes or open file handles in state.
- Before implementing persistence/checkpointers, interrupts, or any API not already used in the codebase, verify it against the **current official LangGraph documentation** and the installed package version.

### 2. SQLite first, two logically separate stores

- **Application database** (`database/app.db`): business records — jobs, file hashes, statuses, timestamps, errors, platform post IDs. Introduced in Milestone 2.
- **LangGraph checkpoint store** (`database/langgraph_checkpoints.db`): graph execution state only, via the official SQLite checkpointer package (`langgraph-checkpoint-sqlite`). Introduced in Milestone 3.
- Never read business data from checkpoints or write business records into the checkpoint DB.
- All application-DB access goes through a small repository layer in `storage/`, so PostgreSQL can replace SQLite later. Do **not** add PostgreSQL without a concrete need (multiple workers, concurrent users, centralized deployment).
- Do not create either database before its milestone.
- **App DB schema (`storage/database.py`, M2 Slice 2.1):** one `jobs` table keyed by the UUID `job_id`, with `platform`, `mode` (`simulated`/`real`), `stem`, file names, `media_sha256` and `caption_sha256` (separate columns), `status`, `platform_post_id`, `destination`, `errors` (JSON list), and UTC `created_at`/`updated_at`. Schema changes are append-only migrations tracked by `PRAGMA user_version`; a database newer than the code is refused. Connections use `synchronous=FULL`, so a committed write is on disk before a publish call.
- **Rules the database enforces itself:** a `simulated` job can never be `PUBLISHED` and a `real` job can never be `SIMULATED`; live statuses (`PUBLISHING`, `PUBLISHED`, `NEEDS_REVIEW`, `SIMULATED`) require both hashes; and a partial unique index allows only **one live job per platform + mode + media hash + caption hash**. `FAILED` jobs never block, so a fixed post can be retried.
- **Duplicates (M2 decisions):** checked per mode, so simulated history never blocks a real publish (D20). A duplicate creates no job row; its files stay in `incoming/` and it is reported each run (D19). `WAITING` (still copying) creates no row either.
- All SQL lives in `storage/` (`database.py`, `jobs.py`); `JobRepository` exposes `create`, `update`, `get`, and `find_blocking_duplicate`, each a committed transaction.

## Reliability rules (non-negotiable)

- A filesystem event does not mean a file is fully copied. Check file stability (e.g. size/mtime unchanged across an interval, file openable) before processing.
- Deduplicate with stable job IDs and content hashes, not filenames alone. **Dedupe key (Milestone 2):** `platform` + `SHA-256(media bytes)` + `SHA-256(caption bytes)`, hashed separately and stored as separate values, never a hash of the raw bytes concatenated together.
- On startup, reconcile `incoming/` against stored job records so interrupted work is recovered.
- Job states are explicit. Current set: `DISCOVERED`, `VALIDATED`, `PUBLISHING`, `PUBLISHED`, `SIMULATED`, `FAILED`, `NEEDS_REVIEW` (refine as needed and document changes here).
- **`SIMULATED` is a terminal state.** A simulated run always ends in `SIMULATED` and never uses `PUBLISHED`. `PublishResult` carries `simulated: bool`, and `PUBLISHED` is only valid when `simulated` is `False` and the platform confirmed the post.
- **`PublishResult` is a definite outcome** (`success`, `simulated`, `platform_post_id`, `error`), with invariants enforced in `__post_init__`: simulated → no post ID; success → no error; failure → non-empty error; real success → post ID required. Its `status` is `SIMULATED`, `PUBLISHED`, or `FAILED`. Ambiguous outcomes are never a `PublishResult`; they set `NEEDS_REVIEW`.
- Archive files **only** after a real, confirmed `PUBLISHED` result; the graph routes to the archive node only on that result. Failed or uncertain jobs keep their files (in `failed/` or `incoming/`) with an actionable error.
- **In Milestone 1, `SIMULATED` jobs leave their files in `incoming/`.** The archive service is still unit-tested directly against temporary directories.
- **Never blindly retry an external publish whose outcome is ambiguous** (timeout, lost response, crash while `PUBLISHING`). Mark it `NEEDS_REVIEW` unless the platform offers a safe way to check whether it succeeded.
- Use structured logging with job ID, platform, and state on every relevant log line.
- **Never log caption text.** Log lines carry job ID, stem, platform, status, paths, and error messages only. The run log is `logs/autoposter.log` (`RotatingFileHandler`, 1 MB × 5 backups, UTC timestamps).
- **Only one `run` at a time.** `run` takes an OS-level lock (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) on `<base>/autoposter.lock` before any work; if held, it prints "Another run is in progress" and exits 4. The OS releases the lock when the process exits or crashes, so a leftover lock file never blocks. `scan` does not take the lock.
- A simulated result must always be labelled as simulated. Never log, store, or display a simulated post as published to LinkedIn.

## Input conventions

- Folders: `data/<Platform>/{incoming,archive,failed}/` for `LinkedIn`, `Facebook`, `Instagram`.
- A post is one image plus one UTF-8 `.txt` caption with the same filename stem, e.g. `product-launch.jpg` + `product-launch.txt`.
- Supported images (initially): `.jpg`, `.jpeg`, `.png`. Video is out of scope for now.
- Handle explicitly: incomplete copies, missing caption, missing media, unsupported extension, empty or whitespace-only caption, undecodable caption.
- **Unpaired files are "pending", not errors.** A lone image or lone caption with no partner is left untouched: it does not become a job and is not moved to `failed/`. It is picked up on a later scan once its partner arrives.
- **Genuine validation errors fail immediately:** unsupported extension, empty or whitespace-only caption, caption that is not valid UTF-8.
- **Pairing rules (`ingestion/scanner.py`):** stems and extensions match case-insensitively; the candidate's `stem` comes from the media filename. Any non-`.txt` file counts as media, so an unsupported extension is still paired and then rejected by validation instead of waiting forever.
- **Ambiguous stems** (more than one media file or caption for one stem, with or without a partner) are reported in `ScanResult.ambiguous`, one sorted group per stem, with one warning per stem. They are left untouched, never become a job, and are not moved to `failed/`; the user must resolve them.
- **Ignored entirely** (neither pending nor candidate): subfolders, dot-files, `~$` lock files, `Thumbs.db`, `desktop.ini`, and partial downloads (`.tmp`, `.part`, `.crdownload`).
- **Stability (`ingestion/stability.py`):** size and `st_mtime_ns` unchanged across the interval, then the file must open for reading. Any `OSError` means "not stable yet", not an error.
- **Slice 5 requirement:** the graph's stability node must check **both** the media file and the caption file before validation runs.
- **Validation (`ingestion/validation.py`)** collects every error. Image: supported extension, not empty, and a file-signature check (JPEG `FF D8 FF`, PNG 8-byte header) whose detected type must match the extension (a PNG named `.jpg` is an error). Caption: strict UTF-8 (a BOM is accepted and stripped), not empty or whitespace-only, read capped at 64 KB (a safety guard, **not** LinkedIn's caption limit). A missing file reports only "not found" for that file. LinkedIn's real caption and image limits are not enforced until verified in M4.
- **Decision (Slice 4): file-signature check, not Pillow.** No dependency; catches renamed and mislabeled files. It does not detect a corrupt image body or read dimensions; revisit Pillow in M4 once LinkedIn's image constraints are verified.
- **Moving posts (`services/archive.py`):** `archive_post(candidate, dest_dir) -> Path` moves both files into a new `dest_dir/<UTC %Y%m%dT%H%M%SZ>_<stem>/` folder (`_2`, `_3`... on a clash) and returns it; used for `archive/` and `failed/`. Moves use `os.replace` and never copy: a destination on a different drive is refused before anything moves. Both files move or neither: a failed caption move puts the image back; if that rollback also fails, it logs CRITICAL and raises `ArchiveError` naming both file locations. It never decides *whether* to move; the graph does.

## Security

- Never commit credentials, tokens, `.env`, databases, content in `data/`, or logs.
- Secrets come from environment variables (loaded from `.env` via `python-dotenv` locally). `.env.example` contains placeholder names only.
- Real LinkedIn integration must use official APIs, current documented permissions, and secure OAuth handling, verified against current official LinkedIn docs when Milestone 4 starts.

## Target code layout

```text
src/social_media_auto_poster/
├── config.py          # Settings loaded from env/.env; paths for data/ and database/
├── models.py          # Shared dataclasses/enums (Platform, JobStatus, PostCandidate, ScanResult, ValidationResult, PublishResult)
├── graph/             # LangGraph only: state.py, nodes.py, workflow.py
├── ingestion/         # Folder scanning, file stability, media/caption pairing, validation
├── storage/           # SQLite repository layer (Milestone 2+)
├── platforms/         # base.py (publisher protocol), simulator, linkedin/ adapter
├── services/          # Hashing, archiving, other plain-Python helpers used by nodes
└── main.py            # CLI entry point
```

The folder structure was migrated in Milestone 1, Slice 1; `config.py` and `models.py` were added in Slice 2. Frozen models hold collections as tuples, not lists, so they are truly immutable. **Contract (changed from the original `list` spec):** `ScanResult.candidates`, `.pending`, `.ambiguous` (and each group inside it), and `ValidationResult.errors` are tuples; passing a list raises `TypeError`. `main.py` has two commands. `scan` (added early, after Slice 3) must stay read-only: it reports stability and validation but never moves, publishes, or creates folders. `run` (Slice 6) creates the data folders, takes the run lock, and runs each complete post once through the graph with the `Simulator` publisher (there is no real publisher to select in M1). It has no `--dir`: incoming, failed, and archive must come from the same settings. It is one-shot; continuous folder watching is not part of M1. One unexpected job error is logged and shown as `ERROR (unexpected)` without stopping other jobs.

**`run` exit codes:** 0 completed (all simulated/waiting, or nothing to do); 1 setup error; 2 usage error (argparse); 3 completed, but at least one job ended `FAILED`, `NEEDS_REVIEW`, or `ERROR`; 4 another run is in progress.

Configuration: `AUTOPOSTER_DATA_DIR` sets the content root (default `data`, relative paths resolved against the base directory, which is the current working directory). Real environment variables override `.env`. `load_settings()` reads `.env` with `dotenv_values` and never mutates `os.environ`. Explain any significant structural change before making it, and do not add empty abstractions.

## Development method

Work **one milestone at a time**. Do not start the next milestone until the current one is tested, its results are explained, and the user has reviewed it.

| # | Milestone | Status |
| --- | --------- | ------ |
| 0 | Inspect, plan, establish this file | Done |
| 1 | Local simulation: package setup, typed state, initial deterministic graph, pairing/validation, simulated publish, tests | Complete, pending review: Slices 0 (docs/packaging), 1 (layout migration), 2 (models + config), 3 (ingestion: scanning + stability), 4 (validation + archive), 5 (LangGraph workflow), 6 (`run` command, end-to-end tests) done and tested. Decisions in `docs/decisions.md` |
| 2 | Durable application records: SQLite schema + repository, job states, hashes, duplicate detection, restart reconciliation | In progress: Slice 2.1 (schema, repository, hashing) done and tested; 2.2 (graph integration: `check_duplicate`, DB writes, `run` opens the DB) next; then 2.3 (startup reconciliation), 2.4 (`jobs` command, `scan` shows duplicates, wrap-up) |
| 3 | Persistent LangGraph execution: SQLite checkpointer, resume/interrupt tests, checkpoint data kept separate | Not started |
| 4 | Real LinkedIn publishing: verify official API, auth, media upload, post creation, ambiguous-outcome safeguards; simulated and real modes clearly separated | Not started |
| 5 | Reliability and expansion: logging/monitoring, operational docs, evaluate PostgreSQL, plan Facebook/Instagram adapters | Not started |

### Milestone 4 requirements (recorded early)

- **An exception raised before any request reaches LinkedIn is a definite `FAILED`, not `NEEDS_REVIEW`** (e.g. missing credentials, a file that cannot be read, a request that could not be built). Nothing was sent, so nothing can be live. The LinkedIn adapter must report these as a failed `PublishResult` (or a dedicated not-sent error the graph maps to `FAILED`), because the M1 graph treats every exception from `publish` as `NEEDS_REVIEW`. Only errors after a request may have reached LinkedIn (timeout, lost response, 5xx with unknown effect) are `NEEDS_REVIEW`.

Every milestone delivers: the files changed, the reasoning behind key decisions, exact Windows (PowerShell) commands to install, run, and test, the expected output, automated tests, and known limitations plus the next step.

Tests must cover, as the features appear: normal execution, validation failures, duplicate detection, and recovery/resume behaviour. Use `pytest` with temporary directories, never the real `data/` folders.

## Dependencies

- `pyproject.toml` is the source of truth for dependencies: runtime in `[project] dependencies`, test tooling in `[project.optional-dependencies] dev`. The supported install is `pip install -e ".[dev]"`, which reads only `pyproject.toml`.
- `requirements.txt` is kept by the developer's choice but is **not** used by the documented install. It currently lists unpinned packages beyond the agreed set (including `langchain`, which is excluded above); do not treat it as authoritative or install from it.
- Add a dependency only in the milestone that uses it. Verify the package name, current version, and Python compatibility on PyPI or the official docs before adding or pinning it. Bounds are `>=` the version actually installed and tested, `<` the next major version.
- Current: `langgraph`, `python-dotenv` (runtime); `pytest` (dev).
- Expected later: `watchdog` (when folder watching is added), `langgraph-checkpoint-sqlite` (M3), `requests` or similar (M4). `langchain` is **not** a dependency (see "No LLMs" above).
- **Python version:** the developer uses **Python 3.14**; minimum is 3.12 (`requires-python = ">=3.12"`). `langgraph` officially lists classifiers only through 3.13, so re-check 3.14 compatibility whenever a dependency is added (especially `langgraph-checkpoint-sqlite` in M3). Falling back to 3.13 is the remedy if something breaks.
- **Virtual environment:** the project venv is named `.vevn` (developer's choice).

## Commands (Windows PowerShell, from repo root)

```powershell
py -3.14 -m venv .vevn               # once
.\.vevn\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
python -m social_media_auto_poster.main scan                 # read-only report of data\LinkedIn\incoming
python -m social_media_auto_poster.main scan --dir <folder> --interval 0
python -m social_media_auto_poster.main run                  # process every complete post once (simulated)
python -m social_media_auto_poster.main run --verbose        # also show INFO logs in the terminal
```

Record every significant decision in `docs/decisions.md` (date, decision, why, consequences).
