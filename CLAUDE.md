# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.
These rules are binding. If `README.md` or other docs disagree with this file, this file wins and the other doc should be corrected.

## Project summary

A Windows-first, folder-driven social media auto-poster. A user drops an image and a matching caption file into a platform's `incoming/` folder; the application detects, validates, tracks, and (eventually) publishes the post through official platform APIs.

- **LinkedIn** is the only platform in scope. Facebook and Instagram have folders only; do not implement them.
- **Publishing is simulated** until Milestone 4. No code may contact LinkedIn (or any platform) before then.
- The developer has intermediate Python/AI experience: favour readable code, explain design decisions, and build incrementally.
- The Python application lives in `backend/`; a React frontend and a FastAPI API are planned but **not built yet** (see "3. Web UI later" below, D32).

## Mandatory architectural decisions

### 1. LangGraph orchestrates the workflow (required)

LangGraph is a **required** part of this project, not an optional or future enhancement. Do not propose replacing it with a hand-written loop or postponing it.

- The per-post workflow is an explicit, deterministic `StateGraph`: typed state, named nodes, explicit edges, and conditional edges where routing depends on results.
- **Discovery and pairing run in `ingestion/`, outside the graph.** Each paired post becomes one graph invocation (one graph thread per job). The graph starts at stability/validation, not discovery.
- Planned graph stages (implemented incrementally, not all at once): stability/validation → duplicate check → prepare → publish (simulated first) → record result → archive on confirmed `PUBLISHED`; plus an error/`NEEDS_REVIEW` path that preserves the files.
- **Implemented graph (`graph/workflow.py`, as of M2 Slice 2.2):** `check_stability` → `validate` → `check_duplicate` → `mark_publishing` → `publish` → `record_result` → (`archive` only on a real `PUBLISHED`) → END. Not stable, or unreadable while hashing → `record_result` (status stays as it was, `retry_later=True`, no DB row). Duplicate (found in `check_duplicate`, or the DB rejects the `mark_publishing` insert) → `record_result` (`duplicate_of` set, no DB row, publisher never called). Invalid, `FAILED`, or `NEEDS_REVIEW` → `move_to_failed` → `record_result` → END. **Every path passes `record_result`, and a real publish is recorded before any file move.** `prepare` (M4) is not added until it has real work.
- **DB writes in the graph:** `mark_publishing` creates the job row as `PUBLISHING` and commits it **before** the publish call. `record_result` writes the final outcome (creating a `FAILED` row first for validation failures). `archive` records the destination best-effort. **Fail closed:** a DB error before publishing propagates (job shows `ERROR (unexpected)`, nothing published, files untouched); a DB error in `record_result` after a real publish propagates and skips `archive`, leaving the `PUBLISHING` row to block a re-publish. `WorkflowContext` carries `repository` and `mode`.
- `mark_publishing` is also a separate node so that, with checkpoints, `PUBLISHING` is saved in graph state before the publish call. Any exception from `publisher.publish` → `NEEDS_REVIEW`, never retried. If archiving fails after `PUBLISHED`, the status stays `PUBLISHED` and the error is recorded. If writing `error-report.txt` fails, it is logged and the outcome is unchanged.
- State is `graph/state.py: JobState` (a `TypedDict` of plain values; `errors` is a list). Live dependencies (publisher, settings, stability interval) go in `WorkflowContext`, passed with `invoke(..., context=...)` (`context_schema`; verified on langgraph 1.2.17). The job ID is a random UUID, also used as the graph `thread_id`; from M2 it is also the primary key of the `jobs` table (decision D18).
- **LangSmith tracing must stay off** (`LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` unset or false): it would send state (paths, captions) to an external service. A test enforces this.
- **LangSmith tracing is OFF by default (D35).** Revisit when AI features are added (caption generation/rewriting, hashtag suggestions, per-platform caption variants) or when the FastAPI app is deployed for shared use. Then enable it with masking of paths, usernames and post IDs, keep it disabled in tests, and never make the app depend on it. Until then, use `autoposter.log` and the `jobs` command for debugging.
- Nodes are thin. Filesystem work, media validation, hashing, database access, and API calls live in ordinary Python service modules; nodes call them and return state updates.
- **No LLMs and no autonomous agents.** Do not add `langchain` model integrations, tool-calling agents, or prompts unless a concrete, agreed requirement justifies it.
- Keep graph state small and serializable: IDs, paths (as strings), statuses, metadata, error details. Never put image bytes or open file handles in state.
- **Checkpoints (M3, `graph/checkpoints.py`, `graph/workflow.py`):** the graph is compiled with `SqliteSaver` on its own connection (`synchronous=FULL`; `SqliteSaver` sets WAL). Every run of a job, first or resumed, uses **`durability="sync"`**, so each step's checkpoint is on disk before the next step starts: the checkpoint saying "publish is next" exists before `publish` is called. `durability` is passed only with a checkpointer (langgraph 1.2.18 raises `AttributeError` for `"sync"` without one). `thread_id` = job ID. A job that ends normally has its thread **deleted** (its record lives in the app DB); a job that raises, or whose process dies, keeps it. `WorkflowContext.checkpointer` carries the store; `None` runs the graph without checkpoints (tests, tools). No `interrupt()` is used: `resolve` handles human review through the app DB. Graph state never holds caption text.
- **Startup resume (`graph/resume.py`, M3):** `run` resumes saved threads **before** reconciling `PUBLISHING` rows. By the saved next step: `check_stability`/`validate`/`check_duplicate`/`mark_publishing` → not resumed (publish provably never started); a `PUBLISHING` row is settled as `FAILED` "interrupted by a crash before publishing; nothing was sent" **in either mode**, and the post is processed again by the same run. `publish` → **never resumed** (outcome unknown); reconciliation decides as before. `record_result`/`move_to_failed`/`archive` → resumed with `invoke(None)`: publish already returned and its result (including a real post ID) is in the checkpoint, so a real job ends `PUBLISHED`, not `NEEDS_REVIEW`. Resumed jobs use their own mode and a publisher that refuses to publish. **Unknown or unreadable** checkpoints (corrupt data, state fields missing from an older/newer format, an unknown next step or none, e.g. a renamed node) are not resumed: a warning is logged, the thread is deleted, and reconciliation decides. A resume that raises keeps its thread for the next run, and reconciliation skips that job (`ERROR (unexpected)`, exit 3). Every other handled thread is deleted. Output: a `RESUMED` section before `RECOVERED`; resumed jobs count toward the summary and exit code. A checkpoint store that cannot be opened stops `run` with exit 1 before anything is processed. `scan`, `jobs`, and `resolve` never open the checkpoint store.
- Before implementing persistence/checkpointers, interrupts, or any API not already used in the codebase, verify it against the **current official LangGraph documentation** and the installed package version.

### 2. SQLite first, two logically separate stores

- **Application database** (`backend/database/app.db`): business records — jobs, file hashes, statuses, timestamps, errors, platform post IDs. Introduced in Milestone 2.
- **LangGraph checkpoint store** (`backend/database/langgraph_checkpoints.db`): graph execution state only, via the official SQLite checkpointer package (`langgraph-checkpoint-sqlite`). Introduced in Milestone 3.
- Never read business data from checkpoints or write business records into the checkpoint DB.
- All application-DB access goes through a small repository layer in `storage/`, so PostgreSQL can replace SQLite later. Do **not** add PostgreSQL without a concrete need (multiple workers, concurrent users, centralized deployment).
- Do not create either database before its milestone.
- **App DB schema (`storage/database.py`, M2 Slice 2.1):** one `jobs` table keyed by the UUID `job_id`, with `platform`, `mode` (`simulated`/`real`), `stem`, file names, `media_sha256` and `caption_sha256` (separate columns), `status`, `platform_post_id`, `destination`, `errors` (JSON list), and UTC `created_at`/`updated_at`. Schema changes are append-only migrations tracked by `PRAGMA user_version`; a database newer than the code is refused. Connections use `synchronous=FULL`, so a committed write is on disk before a publish call.
- **Rules the database enforces itself:** a `simulated` job can never be `PUBLISHED` and a `real` job can never be `SIMULATED`; live statuses (`PUBLISHING`, `PUBLISHED`, `NEEDS_REVIEW`, `SIMULATED`) require both hashes; and a partial unique index allows only **one live job per platform + mode + media hash + caption hash**. `FAILED` jobs never block, so a fixed post can be retried.
- **Duplicates (M2 decisions):** checked per mode, so simulated history never blocks a real publish (D20). A duplicate creates no job row; its files stay in `incoming/` and it is reported each run as `DUPLICATE of job <id8> (<status>, <date>)`, exit code 0 (D19). `WAITING` (still copying or unreadable while hashing) creates no row either. Duplicates are by content, not name: two posts with identical image and caption bytes under different names are duplicates of each other.
- All SQL lives in `storage/` (`database.py`, `jobs.py`); `JobRepository` exposes `create`, `update`, `get`, `find_blocking_duplicate` (each write a committed transaction), and `list_recent`. `open_app_db_read_only` opens an existing database without write access and never creates one.

### 3. Web UI later: FastAPI API + React frontend (D32)

A React frontend and a FastAPI API are planned. Neither exists yet; do not create `frontend/` or `api/` until that work is agreed and started.

- The API will live **inside the Python package**, at `backend/src/social_media_auto_poster/api/`, and reuse the same functions as the CLI (`scan`, `run`, `jobs`, `resolve`): one run path with the run lock, reconciliation, per-mode dedupe, and run logging. **No duplicated logic and no second storage layer**: job data only through `storage/` (`JobRepository`, `open_app_db_read_only`), job IDs are UUID strings.
- FastAPI binds to `127.0.0.1` by default, exposes no secrets (no tokens, `.env` values, or settings beyond paths), and limits CORS to the local frontend origins.
- The React app lives in `frontend/` at the repo root; `frontend/node_modules/` and `frontend/dist/` are Git-ignored.
- Every real-LinkedIn safety rule in this file applies equally to the CLI and the UI: simulated results labelled as simulated, no blind retries, `NEEDS_REVIEW` for ambiguous outcomes, archive only after a confirmed `PUBLISHED`.
- The earlier FastAPI/React code from PR #1 is kept in the tag `archive/pr1-merged-main` (and the unpublished rewire in `archive/api-rewire`) for reference; it is not part of `main` (D31).

## Reliability rules (non-negotiable)

- A filesystem event does not mean a file is fully copied. Check file stability (e.g. size/mtime unchanged across an interval, file openable) before processing.
- Deduplicate with stable job IDs and content hashes, not filenames alone. **Dedupe key (Milestone 2):** `platform` + `SHA-256(media bytes)` + `SHA-256(caption bytes)`, hashed separately and stored as separate values, never a hash of the raw bytes concatenated together.
- On startup, reconcile `incoming/` against stored job records so interrupted work is recovered.
- **Startup reconciliation (`services/reconcile.py`, M2 Slice 2.3):** `run` resolves every `PUBLISHING` row still left after startup resume (M3), after taking the lock and opening the DB, before scanning. Because the lock is held, any `PUBLISHING` row is left over from a crash. **Simulated** → `FAILED` ("nothing was sent"), files stay, and the same run processes the post again. **Real** → `NEEDS_REVIEW`, never retried; the status is written first, then the files move to `failed/` with an error report, but only if both are still in `incoming/` with the recorded hashes (changed or missing files are left alone and noted). A DB error during reconciliation stops the run (exit 1). Recovering a `NEEDS_REVIEW` job makes the run exit 3. Output: a `RECOVERED` section before `JOBS`. `scan` never reconciles. Files in `incoming/` without a row need no reconciliation: the normal run handles them through the duplicate check. Retrying the archive move for a real `PUBLISHED` job left in `incoming/` is deferred to M4.
- Job states are explicit. Current set: `DISCOVERED`, `VALIDATED`, `PUBLISHING`, `PUBLISHED`, `SIMULATED`, `FAILED`, `NEEDS_REVIEW` (refine as needed and document changes here).
- **`SIMULATED` is a terminal state.** A simulated run always ends in `SIMULATED` and never uses `PUBLISHED`. `PublishResult` carries `simulated: bool`, and `PUBLISHED` is only valid when `simulated` is `False` and the platform confirmed the post.
- **`PublishResult` is a definite outcome** (`success`, `simulated`, `platform_post_id`, `error`), with invariants enforced in `__post_init__`: simulated → no post ID; success → no error; failure → non-empty error; real success → post ID required. Its `status` is `SIMULATED`, `PUBLISHED`, or `FAILED`. Ambiguous outcomes are never a `PublishResult`; they set `NEEDS_REVIEW`.
- Archive files **only** after a real, confirmed `PUBLISHED` result; the graph routes to the archive node only on that result. Failed or uncertain jobs keep their files (in `failed/` or `incoming/`) with an actionable error.
- **In Milestone 1, `SIMULATED` jobs leave their files in `incoming/`.** The archive service is still unit-tested directly against temporary directories.
- **Never blindly retry an external publish whose outcome is ambiguous** (timeout, lost response, crash while `PUBLISHING`). Mark it `NEEDS_REVIEW` unless the platform offers a safe way to check whether it succeeded.
- Use structured logging with job ID, platform, and state on every relevant log line.
- **Never log caption text.** Log lines carry job ID, stem, platform, mode, status, paths, and error messages only. The run log is `backend/logs/autoposter.log` (`RotatingFileHandler`, 1 MB × 5 backups, UTC timestamps).
- **Run log structure:** every log-file line carries `run_id=<8 hex>` (set per `run`/`resolve` by a `ContextVar` + logging filter, so graph nodes get it too; `-` outside a run). Terminal lines stay short (no run_id). `run` logs "Run started" first (right after taking the lock) and always ends with "Run finished" giving `simulated published failed needs_review duplicates waiting pending ambiguous recovered resumed errors exit_code duration_s`. Every job line has `mode=`. **A job_id is only logged if it is stored:** duplicates and files still being copied log exactly one `Job skipped: ... status=SKIPPED result=DUPLICATE duplicate_of=<stored id>` / `result=WAITING` line with no job_id. The terminal prints "Processing ..." before any job runs.
- **Only one `run` at a time.** `run` takes an OS-level lock (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) on `<base>/autoposter.lock` before any work; if held, it prints "Another run is in progress" and exits 4. The OS releases the lock when the process exits or crashes, so a leftover lock file never blocks. `resolve` takes the same lock (so it never changes the DB during a run); `scan` and `jobs` do not.
- A simulated result must always be labelled as simulated. Never log, store, or display a simulated post as published to LinkedIn.

## Input conventions

- Folders: `data/<Platform>/{incoming,archive,failed}/` (by default `backend/data/...`) for `LinkedIn`, `Facebook`, `Instagram`.
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
- **Moving posts (`services/archive.py`):** `archive_post(candidate, dest_dir, *, job_id, folder_name) -> Path` moves both files into `dest_dir/<folder_name>/` (`_2`, `_3`... if that folder belongs to another job) and returns it; used for `archive/` and `failed/`. In the graph, `folder_name` is `JobState.folder_name` = `<job start UTC %Y%m%dT%H%M%SZ>_<stem>`, fixed when the job starts so a resumed move looks in the same place; reconciliation uses the current time. Every new folder gets a **`.autoposter-job` marker** holding the job ID, written before any file moves. Moves use `os.replace` and never copy: a destination on a different drive is refused before anything moves. First call: both files move or neither (a failed caption move puts the image back; if that rollback also fails, it logs CRITICAL and raises `ArchiveError` naming both file locations). **Safe to repeat for the same job (M3):** the job's folder is found by its marker (an empty folder left by a crash right after `mkdir` is claimed); files already there count as moved and the rest are moved now; a file in neither place, or one that cannot be moved, raises `ArchiveError` naming both locations, for manual repair. Another job's folder, or an older folder without a marker, is never taken over. A failed archive move never changes a `PUBLISHED` job's status. It never decides *whether* to move; the graph does.

## Security

- Never commit credentials, tokens, `.env`, databases, content in `data/`, logs, or `sandbox/`.
- Secrets come from environment variables (loaded from `backend/.env` via `python-dotenv` locally). `backend/.env.example` contains placeholder names only.
- Real LinkedIn integration must use official APIs, current documented permissions, and secure OAuth handling, verified against current official LinkedIn docs when Milestone 4 starts.

## Code layout

```text
social-media-auto-poster/
├── backend/                       # The Python project (D32)
│   ├── pyproject.toml             # Package and dependencies; readme = backend/README.md
│   ├── README.md                  # Backend setup, run, test, sandbox tester
│   ├── .env.example               # Copy to backend/.env (never committed)
│   ├── src/social_media_auto_poster/
│   │   ├── config.py              # Settings; base folder = backend/ (see "Configuration")
│   │   ├── models.py              # Shared dataclasses/enums (Platform, JobStatus, PostCandidate, ScanResult, ValidationResult, PublishResult)
│   │   ├── graph/                 # LangGraph only: state.py, nodes.py, workflow.py
│   │   ├── ingestion/             # Folder scanning, file stability, media/caption pairing, validation
│   │   ├── storage/               # SQLite repository layer
│   │   ├── platforms/             # base.py (publisher protocol), simulator, linkedin/ adapter
│   │   ├── services/              # Hashing, archiving, reconciliation, run lock
│   │   └── main.py                # CLI entry point
│   │                              # (api/ — FastAPI, added later, not now)
│   ├── tests/unit/, tests/integration/, tests/conftest.py
│   ├── scripts/app.py             # Sandbox tester
│   └── data/ database/ logs/ sandbox/ .env autoposter.lock   # Runtime, Git-ignored
├── docs/decisions.md              # Decision records
├── docs/linkedin-api-notes.md     # What LinkedIn's official docs say (M4), with sources
│                                  # (frontend/ — React, created only when frontend work starts)
├── CLAUDE.md  README.md  .gitignore
└── .vevn/                         # Virtual environment (repo root, Git-ignored)
```

The folder structure was migrated in Milestone 1, Slice 1, and moved from `src/` to `backend/src/` in D32; `config.py` and `models.py` were added in Slice 2. Frozen models hold collections as tuples, not lists, so they are truly immutable. **Contract (changed from the original `list` spec):** `ScanResult.candidates`, `.pending`, `.ambiguous` (and each group inside it), and `ValidationResult.errors` are tuples; passing a list raises `TypeError`. `main.py` has four commands (`run`, `scan`, `jobs`, `resolve`). `jobs` (Slice 2.4) is read-only history via `open_app_db_read_only` (`--last N`, `--status S`, `--id PREFIX`; prefix matching has no wildcards); it never creates or changes the DB and takes no lock. `scan` (added early, after Slice 3) must stay read-only: it reports stability, validation, and (for stable valid posts) `new` or `DUPLICATE of job …` using the same `sha256_file` + `find_blocking_duplicate` check as `run` in the same mode (`scan --real` checks against real-mode history and marks posts `run --real` would refuse), plus a note about interrupted `PUBLISHING` jobs; it never moves, publishes, reconciles, or creates folders or the DB. `run` (Slice 6) creates the data folders, takes the run lock, and runs each complete post once through the graph with the `Simulator` publisher, or with the real publisher only when `--real` is given (M4, D38). It has no `--dir`: incoming, failed, and archive must come from the same settings. It is one-shot; continuous folder watching is not part of M1. One unexpected job error is logged and shown as `ERROR (unexpected)` without stopping other jobs.

**`run` exit codes:** 0 completed (all simulated, duplicate, or waiting, or nothing to do); 1 setup error (data folder, job database, checkpoint store, or crash recovery), or `run --real` refused before publishing anything (D38); 2 usage error (argparse); 3 completed, but at least one job ended `FAILED`, `NEEDS_REVIEW`, or `ERROR`, or a crashed job was recovered as `NEEDS_REVIEW`; 4 another run is in progress. **`jobs` exit codes:** 0 ok (including "no jobs yet"); 1 database unreadable or `--id` matches no job or more than one; 2 usage error.

**`resolve <job_id> --published <post_id> | --failed`:** settles a `NEEDS_REVIEW` job after the user has checked the platform. Only `NEEDS_REVIEW` jobs; the repository UPDATE only matches a row that is still `NEEDS_REVIEW`, so no other row can change. Job-ID prefix of at least 6 characters (shorter is a usage error) matching exactly one job. `--published` needs a non-blank post ID, sets `PUBLISHED` (content stays blocked) and is refused for simulated jobs; `--failed` sets `FAILED` (content can be retried). A note `resolved manually as <STATUS> [(post ID ...)] at <UTC>` is appended to the job's errors. Files are never moved; the output says where they are and what to do next. Never creates the DB. **Exit codes:** 0 resolved; 1 refused (no match, ambiguous, wrong status, blank post ID, simulated `--published`, no DB); 2 usage error; 4 lock held.

**Configuration:** the base directory is `backend/`, found from the package's own location (`config.PROJECT_DIR`), **not** the current working directory, so every command uses `backend/.env`, `backend/data/`, `backend/database/`, `backend/logs/`, and `backend/autoposter.lock` whichever folder it is started from. Only editable installs are supported: without `backend/pyproject.toml`, `load_settings()` raises `ProjectDirNotFoundError` rather than writing into site-packages. `AUTOPOSTER_DATA_DIR` sets the content root (default `data`, relative paths resolved against the base directory). `AUTOPOSTER_BASE_DIR` moves the whole base directory; it is read from the **process environment only, never from `.env`**, and exists for the tests and the sandbox tester. Real environment variables override `.env`. `load_settings()` reads `.env` by its path in the base directory with `dotenv_values` and never mutates `os.environ`. Explain any significant structural change before making it, and do not add empty abstractions.

## Development method

Work **one milestone at a time**. Do not start the next milestone until the current one is tested, its results are explained, and the user has reviewed it.

### Git workflow (from 2026-10-28)

Work **directly on `main`**: no new branches and no pull requests. For every change:

1. `git pull origin main` first.
2. Run `python -m pytest -q` (from `backend\`) before **every** commit; commit only if all tests pass.
3. Make small commits with clear messages.
4. Never force-push and never rewrite history (no `reset` of pushed commits, no `rebase` of `main`, no `--amend` of a pushed commit).
5. Show the developer the test output and the list of new commits, and **push only after the developer says OK**.

`backup/main-before-pr1` and every `archive/*` tag must be kept.

| # | Milestone | Status |
| --- | --- | --- |
| 0 | Inspect, plan, establish this file | Done |
| 1 | Local simulation: package setup, typed state, initial deterministic graph, pairing/validation, simulated publish, tests | **Complete** (signed off 2026-10-28): Slices 0 (docs/packaging), 1 (layout migration), 2 (models + config), 3 (ingestion: scanning + stability), 4 (validation + archive), 5 (LangGraph workflow), 6 (`run` command, end-to-end tests). Decisions in `docs/decisions.md` |
| 2 | Durable application records: SQLite schema + repository, job states, hashes, duplicate detection, restart reconciliation | **Complete** (signed off 2026-10-28 after the M2 review; see the detail below) |
| 3 | Persistent LangGraph execution: SQLite checkpointer, resume/interrupt tests, checkpoint data kept separate | **Complete** (signed off 2026-10-28; D34) |
| 4 | Real LinkedIn publishing: verify official API, auth, media upload, post creation, ambiguous-outcome safeguards; simulated and real modes clearly separated | **In progress** (D36): 4.0 docs research and design done; 4.1 `--real` switch + D33 safeguards done (D38, fake publisher); next 4.2 exact bytes |
| 5 | Reliability and expansion: logging/monitoring, operational docs, evaluate PostgreSQL, plan Facebook/Instagram adapters | Not started |

### Milestone 2 status detail

| Work | Implemented | Tested | Committed |
| --- | --- | --- | --- |
| 2.1 Schema, migrations, repository, hashing | yes | yes | `5237e69` |
| 2.2 `check_duplicate`, DB writes in the graph, fail-closed rules, `run` opens the DB | yes | yes | `24c73d7` |
| Dev tools: `scripts/app.py` sandbox tester, `open_app_db_read_only`, `list_recent` (a workflow notebook was added here and later removed, D32) | yes | yes | `04ce5cb` |
| 2.3 Startup reconciliation, `list_by_status`, tester crash simulation (menu 11) | yes | yes | `d30dc71` |
| 2.4 `jobs` command, duplicates + interrupted note in `scan`, tester history via `jobs` | yes | yes | `9c0e4be` |
| M2 follow-up: `resolve` command (+ tester menu 12), run logging (run_id, start/finish, skipped lines, mode), sandbox hardening, `/database/` ignored, test isolation (`tests/conftest.py`) | yes | yes | `5e14b18` |
| PR #1 (`backend/` move + FastAPI/React) undone: `main` restored to `5e14b18` (D31) | yes | yes | `ba79243` (PR #3) |
| Restructure into `backend/`, base folder from the package location, notebook and `requirements.txt` removed (D32) | yes | yes | `783d330` (PR #4) |
| Legacy root runtime folders removed from `.gitignore` after the manual move | yes | n/a | `a4398b5` (PR #5) |

### Milestone 3 status detail

| Work | Implemented | Tested | Committed |
| --- | --- | --- | --- |
| 3.1 Separate checkpoint store (`graph/checkpoints.py`), dependency, `Settings.checkpoint_db_path` | yes | yes | `5ec4a7f` |
| 3.2 Moves safe to repeat: fixed folder name in state, `.autoposter-job` marker | yes | yes | `9403ce3` |
| 3.3 Graph compiled with the checkpointer, `durability="sync"`, finished threads deleted, `run` opens the store | yes | yes | `c418389` |
| 3.4 Startup resume (`graph/resume.py`), `RESUMED` output, unknown checkpoints not resumed | yes | yes | `a90c8e5` |
| 3.5 Crash tests: interrupted moves, real process kill | yes | yes | `17f1fd3` |
| 3.6 Tester menu 13 (crash after publish) | yes | yes | `6885e20` |

### Milestone 4 requirements (recorded early)

- **An exception raised before any request reaches LinkedIn is a definite `FAILED`, not `NEEDS_REVIEW`** (e.g. missing credentials, a file that cannot be read, a request that could not be built). Nothing was sent, so nothing can be live. The LinkedIn adapter must report these as a failed `PublishResult` (or a dedicated not-sent error the graph maps to `FAILED`), because the M1 graph treats every exception from `publish` as `NEEDS_REVIEW`. Only errors after a request may have reached LinkedIn (timeout, lost response, 5xx with unknown effect) are `NEEDS_REVIEW`.
- **The first real run must not silently publish posts that were only simulated** (M2 review, D33). Simulated posts stay in `incoming/`, and duplicates are checked per mode (D20), so without a safeguard switching to real mode would post everything still in `incoming/`. Before publishing for real, `run` must refuse, or require an explicit confirmation, while `incoming/` holds posts whose content already has a `SIMULATED` job, and say which posts and how to proceed.
- **The job database is the only protection against double-posting** (M2 review, D33). Real mode must refuse to start when the job database is new or has no jobs while `archive/` already holds published posts, and the docs must explain backing up `backend/database/app.db` (and restoring it) before real publishing.
- **Publish exactly the bytes that were hashed** (M2 review, D33). `check_duplicate` hashes the files and `publish` reads them again later, so a file replaced in between would be posted under the recorded hashes. The real publisher must upload the same bytes that were hashed (read once), or re-hash immediately before the upload and treat a mismatch as a definite `FAILED` that sends nothing.

- **Milestone 4 design (D36, binding; facts and sources in `docs/linkedin-api-notes.md`):**
  - **Target:** the developer's **personal profile** only: LinkedIn products "Share on LinkedIn" (`w_member_social`) and "Sign In with LinkedIn using OpenID Connect" (`openid profile`); author `urn:li:person:{sub}` from `GET /v2/userinfo`. One image + caption per post.
  - **Token:** generated by hand in the LinkedIn Developer Portal and stored in `backend/.env` as `LINKEDIN_ACCESS_TOKEN`; optional `LINKEDIN_TOKEN_CREATED` (date). Tokens last 60 days and are not refreshed automatically: `linkedin-check` and `run --real` **warn** within 7 days of expiry and **fail clearly** once expired.
  - **Real mode only via `--real`** (`run --real`, and `scan --real` for its duplicate check), **never from `.env`** or any setting, so a scheduled task cannot switch to real posting by accident.
  - **`run --real` safeguards (slice 4.1, D38, implementing D33):** before anything changes (startup recovery included) it needs a real publisher (`main._real_publisher`; `None` until slices 4.3-4.4 → "real publishing is not set up yet") and refuses when `archive/` holds post folders while the job database has no real job or does not exist (restore `app.db`; no override). After scanning, it refuses when `incoming/` holds posts whose content was only simulated (a `SIMULATED` job and no live real job), listing them; `--publish-simulated` (only with `--real`; alone it is a usage error, exit 2) confirms them. Every refusal publishes nothing and exits 1. Real runs print a `REAL MODE` banner; footers say what this run sent ("Simulation mode: nothing was sent to LinkedIn in this run." / "Real mode: N post(s) published to LinkedIn in this run." plus how many need review).
  - **HTTP:** `httpx`, declared explicitly in `pyproject.toml`. **No automatic retries** for creating a post: no client or transport retries on `POST /rest/posts`, and a test proves it is called exactly once.
  - **Outcomes of `POST /rest/posts`:** `201` with `x-restli-id` → `PUBLISHED` with that post ID. `ConnectError`/`ConnectTimeout` (request never sent) → `FAILED`. 400/401/403/404/422/429 → `FAILED` (401: token expired or invalid). `ReadTimeout`, `WriteTimeout`, `RemoteProtocolError`, a dropped connection, any 5xx, 409, or `201` without `x-restli-id` → `NEEDS_REVIEW`, never retried. Everything before this call (missing/expired token, unreadable file, hash mismatch, caption too long, image registration or upload failure) → `FAILED`: nothing was posted. Each case is tested with fake transports.
  - **Image upload:** Images API (`initializeUpload`, then `PUT` the bytes). `linkedin-check` first verifies that image status can be read with the token; only if it cannot, use the Assets API's synchronous upload as a fallback (needs a recorded decision).
  - **Caption:** escape every reserved little-format character (`| { } @ [ ] ( ) < > # \ * _ ~`) except `#` that starts a hashtag (`#word`); `@` is always plain text. Real mode rejects captions over **3,000 characters** (LinkedIn's documented UGC limit; the Posts API states none) before any upload.
  - **Image pixel limit** (< 36,152,320 pixels, D37): real mode reads width and height from the PNG `IHDR` chunk or the JPEG start-of-frame marker with the **standard library only** (no Pillow) and rejects larger images, or images whose size cannot be read, as `FAILED` before any upload. Do not add Pillow without the developer's approval.
  - **`Linkedin-Version`** lives in **one constant** (currently `202610`). Update it at least yearly or when LinkedIn announces its sunset, then run the tests and `linkedin-check`.
  - **Secrets:** the access token is never logged, never in graph state or checkpoints, never in error reports or the job database. A test scans logs, state, checkpoints, and reports for it.
  - **Uncertain publishes cannot be checked automatically** (`r_member_social` is restricted to approved users): they stay `NEEDS_REVIEW` until the developer checks LinkedIn and runs `resolve`.

Every milestone delivers: the files changed, the reasoning behind key decisions, exact Windows (PowerShell) commands to install, run, and test, the expected output, automated tests, and known limitations plus the next step.

Tests must cover, as the features appear: normal execution, validation failures, duplicate detection, and recovery/resume behaviour. Use `pytest` with temporary directories, never the real `data/` folders.

## Dependencies

- `backend/pyproject.toml` is the only source of truth for dependencies: runtime in `[project] dependencies`, test tooling in `[project.optional-dependencies] dev`. The supported install is `pip install -e ".[dev]"` from `backend/`. There is no `requirements.txt` (removed in D32); do not re-add one.
- Add a dependency only in the milestone that uses it. Verify the package name, current version, and Python compatibility on PyPI or the official docs before adding or pinning it. Bounds are `>=` the version actually installed and tested, `<` the next major version.
- Current: `langgraph`, `langgraph-checkpoint-sqlite` (`>=3.1.2,<4`, M3; past the CVE-2025-67644 fix, and it requires `langgraph-checkpoint>=4.3.0`, past the CVE-2025-64439 fix), `python-dotenv` (runtime); `pytest` (dev). Verified on Python 3.14.8 with langgraph 1.2.18.
- Expected later: `httpx` (M4 slice 4.3, declared explicitly in `pyproject.toml`; decided in D36), `watchdog` (when folder watching is added). **No Pillow** (D37) unless the developer approves it. `langchain` is **not** a dependency (see "No LLMs" above).
- **Python version:** the developer uses **Python 3.14**; minimum is 3.12 (`requires-python = ">=3.12"`). `langgraph` officially lists classifiers only through 3.13, so re-check 3.14 compatibility whenever a dependency is added (`langgraph-checkpoint-sqlite` 3.1.2 was verified in M3). Falling back to 3.13 is the remedy if something breaks.
- **Virtual environment:** the project venv is named `.vevn` (developer's choice) and lives at the repo root, shared by the whole repo; the planned frontend uses Node, not Python.

## Developer tools

- **`backend/scripts/app.py`** (stdlib only): interactive manual tester. It always works in a sandbox (default `backend/sandbox`, Git-ignored) that acts as both the base folder and the data folder (it sets `AUTOPOSTER_BASE_DIR` and `AUTOPOSTER_DATA_DIR` while a command runs, then restores them), so its database, logs, and run lock never touch the real ones. It refuses a sandbox that is the project folder, or that is, contains, or is inside the real data folder, the real `database/` folder, or the real `logs/` folder. It only adopts a folder that is **empty or already holds the `.autoposter-sandbox` marker** (so `--sandbox src` is refused), and Reset deletes only a marked folder. Run, Scan, Show job history, and Resolve call `main(["run"])`, `main(["scan"])`, `main(["jobs"])`, and `main(["resolve", ...])`; never duplicate app logic in the script. Menu 11 simulates a crash during publish (a `PUBLISHING` row only, so reconciliation handles it); menu 12 resolves a `NEEDS_REVIEW` job; menu 13 simulates a crash **after** publish (the real graph with the sandbox's stores, stopped before `record_result`), so the next Run resumes it. Real mode in menu 13 uses a sandbox-only fake publisher (`urn:li:share:sandbox-...`) that sends nothing.
- **Test isolation (`backend/tests/conftest.py`):** every test runs from its own empty temporary folder, which is also its base folder (`AUTOPOSTER_BASE_DIR`), with `AUTOPOSTER_DATA_DIR` unset; a session-wide check fails the run if the real `backend/database/app.db` or `backend/database/langgraph_checkpoints.db` was changed or created. A test that needs its own base folder must set `AUTOPOSTER_BASE_DIR` (changing the current folder alone no longer moves the base).

## Commands (Windows PowerShell)

```powershell
py -3.14 -m venv .vevn               # once, from the repo root
.\.vevn\Scripts\Activate.ps1         # from the repo root
cd backend
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
python scripts\app.py                                        # sandbox tester (backend\sandbox)
python -m social_media_auto_poster.main scan                 # read-only report of data\LinkedIn\incoming
python -m social_media_auto_poster.main scan --dir <folder> --interval 0
python -m social_media_auto_poster.main run                  # process every complete post once (simulated)
python -m social_media_auto_poster.main run --real           # publish to LinkedIn for real (M4; refused until the publisher is set up)
python -m social_media_auto_poster.main scan --real          # preview with the real-mode duplicate check
python -m social_media_auto_poster.main run --verbose        # also show INFO logs in the terminal
python -m social_media_auto_poster.main jobs                 # read-only job history (--last N, --status S, --id PREFIX)
python -m social_media_auto_poster.main resolve <id> --failed            # settle a NEEDS_REVIEW job (or --published <post_id>)
```

Record every significant decision in `docs/decisions.md` (date, decision, why, consequences).
