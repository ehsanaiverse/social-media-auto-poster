# Social Media Auto-Poster

A local, folder-driven Python application scaffold for preparing and publishing social media content. The project is being built incrementally, starting with LinkedIn and adding other platforms only after their integrations are separately planned and validated.

The Python application lives in **`backend/`**; its setup, commands, tests, and sandbox tester are summarised in [backend/README.md](backend/README.md). A local FastAPI API (`backend/app/`, started with `python -m app` from `backend/`) and a React dashboard (`frontend/`) are built; see [backend/README.md](backend/README.md#local-api) for how to run them. The API is optional and binds to `127.0.0.1` only.

## MVP goals

- Discover content placed in platform-specific incoming folders.
- Pair image files with UTF-8 caption files and validate them before processing.
- Track post state and processing history so interrupted work can be recovered safely.
- Publish through official platform APIs, archive only confirmed successes, and retain failures with useful diagnostics.
- Prevent duplicate processing and avoid blindly retrying requests whose publishing outcome is ambiguous.

## Non-goals for this scaffold

There is no continuous folder watcher, scheduler, OAuth flow, or live publishing integration yet. The simulator does not contact a platform and its result is not confirmation of publication. This scaffold does not claim API access is configured.

## Current status

**Milestones 1 (local simulation) and 2 (job database) complete and signed off (2026-10-28); Milestone 3 (crash-safe checkpoints) implemented and awaiting review.** What exists today:

- Packaging (`backend/pyproject.toml`) with declared dependencies.
- An environment-variable template (`backend/.env.example`).
- Shared models (`models.py`): `Platform`, `JobStatus` (including the terminal `SIMULATED` status), `PostCandidate`, `ScanResult`, `ValidationResult`, and `PublishResult`, which records a definite success or failure and always whether the result was simulated.
- Settings (`config.py`): uses `backend/` as the base folder whatever folder a command is started from, resolves the content folder root, and can create the `data/<Platform>/{incoming,archive,failed}` folders.
- A platform-neutral publisher protocol (`platforms/base.py`).
- A safe simulation publisher that never contacts any platform (`platforms/simulator.py`).
- Ingestion (`ingestion/`): a one-shot scan that pairs images with captions (`scanner.py`), a file-copy stability check (`stability.py`), and content validation (`validation.py`).
- A service that moves a post's two files together into `archive/` or `failed/` (`services/archive.py`).
- A LangGraph workflow (`graph/`) that takes one post through stability → validation → duplicate check → simulated publish → record, moving invalid or failed posts to `failed/` with an `error-report.txt`. Simulated posts stay in `incoming/`.
- A job database (`backend\database\app.db`, SQLite, via `storage/`): every processed post is recorded with SHA-256 hashes of its image and caption, so the same content is never processed twice.
- Crash recovery: at the start of every `run`, jobs left half-published by a crash are resolved (see `RECOVERED` below).
- Graph checkpoints (`backend\database\langgraph_checkpoints.db`, a separate SQLite file): each step of a job is saved before the next one starts, so a run that crashed **after** publishing is finished by the next run instead of being marked `NEEDS_REVIEW` (see `RESUMED` below). A crash during the publish itself is never retried.
- Four commands (`main.py`): `run` processes every complete post once, the read-only `scan` (with duplicate check) and `jobs` (history), and `resolve` for jobs that need review. See "How to use" below.
- Unit and integration tests under `backend/tests/`. Decisions are recorded in `docs/decisions.md`.

Platform order:

1. LinkedIn — first integration milestone; not implemented.
2. Facebook — planned, not implemented.
3. Instagram — planned, not implemented.

Facebook and Instagram are placeholders for future work, not supported integrations.

## Project layout

Packages marked "empty" contain only an `__init__.py`:

```text
backend/                           The Python project
  pyproject.toml, README.md, .env.example
  src/social_media_auto_poster/
    config.py                      Settings (base folder = backend/) and data-folder creation
    models.py                      Platform, JobStatus, PostCandidate, ScanResult, ValidationResult, PublishResult
    platforms/                     Publisher protocol (base.py) and simulator (simulator.py)
    platforms/{linkedin,facebook,instagram}/   Empty
    graph/                         LangGraph workflow: state.py, nodes.py, workflow.py
    ingestion/                     Scanning/pairing, stability check, validation
    services/                      Hashing, moving posts, crash recovery, single-run lock
    storage/                       SQLite job database and repository
    main.py                        Commands: run, scan, jobs, resolve
  tests/unit/, tests/integration/  Unit tests; full-graph and end-to-end run tests
  scripts/app.py                   Interactive sandbox tester
  data/                            Local platform content; ignored by Git
    LinkedIn/{incoming,archive,failed}/   (and Facebook/, Instagram/)
  database/, logs/, sandbox/       Job database, run log, tester sandbox; ignored by Git
docs/decisions.md                  Decision records
```

The binding layout is in `CLAUDE.md` ("Code layout").

## Content naming convention

The initial convention is one image and one UTF-8 text caption with the same filename stem, for example:

```text
backend/data/LinkedIn/incoming/product-launch.jpg
backend/data/LinkedIn/incoming/product-launch.txt
```

The planned initial image extensions are `.jpg`, `.jpeg`, and `.png`. Pairing, the file-copy stability check, and validation (extension, image signature, UTF-8 non-empty caption) exist; see `CLAUDE.md`, "Input conventions". This convention is provisional; media support will be reviewed against each platform's official API before implementation.

## Windows setup

Use Python 3.12 or newer; the project is developed on **Python 3.14**. The virtual environment `.vevn` stays at the repo root; the package is installed from `backend\`:

```powershell
py -3.14 -m venv .vevn               # once, from the repo root
.\.vevn\Scripts\Activate.ps1         # from the repo root
cd backend
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Dependencies are declared only in `backend\pyproject.toml`. The `[dev]` extra adds `pytest`. Install in editable mode (`-e`): the application keeps its data, database, logs, and `.env` in `backend\`, whichever folder you run a command from.

Verify the environment:

```powershell
python --version
python -m pip check
python -m pytest
```

## How to use (Milestone 1: simulation only)

1. Put an image and a caption with the same name in `backend\data\LinkedIn\incoming\`, e.g. `product-launch.jpg` + `product-launch.txt` (UTF-8 text; `.jpg`, `.jpeg`, or `.png`).
2. Optional preview, changes nothing: `python -m social_media_auto_poster.main scan`
3. Process: `python -m social_media_auto_poster.main run`

Example `run` output:

```text
Processing LinkedIn incoming: D:\social-media-auto-poster\backend\data\LinkedIn\incoming

JOBS (3)
  animated         FAILED                      moved to LinkedIn\failed\20261025T153102Z_animated
                     - unsupported image type '.gif' (allowed: .jpg, .jpeg, .png)
  product-launch   SIMULATED (not published)   files stay in incoming/
  team             SIMULATED (not published)   files stay in incoming/

PENDING (1) - waiting for a partner file
  lonely.jpg

Simulation mode: nothing was sent to LinkedIn in this run.
```

What happens to each post:

| Result | Meaning | Files |
| --- | --- | --- |
| `SIMULATED (not published)` | Valid; publishing was simulated. **Nothing is sent to LinkedIn until Milestone 4.** | Stay in `incoming/`; recorded in the job database |
| `DUPLICATE` | Same image and caption content as an earlier job (even under a different file name) | Untouched; reported on every run until you delete them |
| `FAILED` | Invalid (or a definite publish failure) | Moved to `failed\<UTC time>_<name>\` with `error-report.txt` explaining why |
| `NEEDS_REVIEW` | Publish outcome unknown (cannot happen with the simulator) | Moved to `failed\...`; check LinkedIn before re-posting |
| `RESUMED` | Shown first when an earlier run crashed after a publish had returned (while recording the result or moving the files). The job is finished from its saved checkpoint; nothing is published again | Moved or kept as for any finished job; a half-finished move is completed |
| `RECOVERED` | Shown before the jobs when an earlier run crashed mid-publish. A simulated job becomes `FAILED` (nothing was sent) and is processed again in the same run; a real one becomes `NEEDS_REVIEW` and is never retried | Simulated: stay in `incoming/`. Real: moved to `failed\...` with a report, if unchanged |
| `WAITING (still copying)` | A file is still being written | Untouched; retried next run |
| `PENDING` / `AMBIGUOUS` | No partner file yet / more than one file per name | Untouched |

Every folder in `failed\` and `archive\` contains a small `.autoposter-job` file with the job ID; it lets an interrupted move be finished safely. To retry a failed post: fix it, move its image and caption back to `incoming\`, and delete the old `failed\` folder. A failed job never blocks a retry; editing the caption (or image) of any post makes it a new job.

`run` options: `--verbose` shows INFO logs in the terminal; `--interval 0` skips the 1-second change-watch wait. The full log is `backend\logs\autoposter.log` (UTC timestamps, rotated at 1 MB, 5 backups; caption text is never logged). Each line carries the run's `run_id`; every run starts with `Run started` and ends with a `Run finished` line that counts each outcome and gives the exit code and duration. Only one `run` can work at a time.

`run` exit codes (useful with Task Scheduler): `0` all simulated/duplicate/waiting or nothing to do, `1` setup error (including a job database that cannot be opened or crash recovery that cannot complete), `2` invalid command or option, `3` at least one job `FAILED`/`NEEDS_REVIEW`/`ERROR` (or a crashed job recovered as `NEEDS_REVIEW`), `4` another run is in progress.

### Real mode: `run --real` (Milestone 4, in progress)

Real publishing is only ever chosen on the command line with `--real`; there is no setting for it, so a scheduled task can never switch to real posting by accident. Real posts go to your own LinkedIn profile, with the access token from `backend\.env` (see `backend\.env.example`: `LINKEDIN_ACCESS_TOKEN`, and `LINKEDIN_TOKEN_CREATED` for expiry warnings; tokens last 60 days). Check the setup first, without posting anything:

```powershell
python -m social_media_auto_poster.main linkedin-check                 # token, expiry, and who posts would be published as
python -m social_media_auto_poster.main linkedin-check --upload-test   # also uploads a test image (never used in a post)
```

A real run checks the token with LinkedIn before anything else; a missing, expired, or rejected token stops it (exit code 1, nothing published). It warns from 7 days before the token expires. A post whose outcome is uncertain (for example, a timeout after sending) is marked `NEEDS_REVIEW` and never retried: check your LinkedIn profile, then use `resolve`.

Before publishing anything, `run --real` refuses (exit code 1, nothing published) when:

- `incoming\` holds posts that were **only simulated** so far. They are listed; remove the ones you do not want on LinkedIn, or confirm with `run --real --publish-simulated`.
- `archive\` holds published posts but the job database has **no record of any real job** (it may have been replaced or lost). Restore your backup of `backend\database\app.db`; without it, posts could be published twice.

`scan --real` previews this: it checks duplicates against real-mode history and marks the posts `run --real` would refuse.

### `scan` (read-only)

`scan` reports what `run` would pick up and never moves or publishes anything:

```powershell
python -m social_media_auto_poster.main scan                          # data\LinkedIn\incoming (respects AUTOPOSTER_DATA_DIR)
python -m social_media_auto_poster.main scan --dir C:\some\folder     # any folder
python -m social_media_auto_poster.main scan --interval 0             # instant: checks locks, skips the change-watch wait
python -m social_media_auto_poster.main scan --help
```

It lists **candidates** (image + caption pairs, each marked stable or not, valid or `INVALID:` with reasons, and for stable valid posts `new` or `DUPLICATE of job …`), **pending** files waiting for a partner, and **ambiguous** stems that need fixing by hand. If the job database has jobs interrupted by a crash, it notes that the next `run` will recover them. `scan` reads the job database read-only and never creates it. Exit code 0 means the scan completed; 1 means the folder does not exist.

### `resolve` (settle a NEEDS_REVIEW job)

A `NEEDS_REVIEW` job (publish outcome unknown) is never retried automatically. After you have checked LinkedIn:

```powershell
python -m social_media_auto_poster.main resolve 8a61f3 --published urn:li:share:123   # it is live: record the post ID; content stays blocked
python -m social_media_auto_poster.main resolve 8a61f3 --failed                       # it is not live: allow a retry
```

Use at least 6 characters of the job ID (from `jobs`). Only `NEEDS_REVIEW` jobs can be resolved; a simulated job cannot be marked published. A timestamped note is added to the job (see `jobs --id`). Files are never moved: the output tells you where they are and, for `--failed`, how to retry (move them back into `incoming\`). If a `run` is in progress, it refuses (exit 4). Exit codes: `0` resolved, `1` refused (nothing changed), `2` invalid arguments, `4` a run is in progress.

### `jobs` (read-only history)

```powershell
python -m social_media_auto_poster.main jobs                    # 20 most recent jobs, newest first
python -m social_media_auto_poster.main jobs --last 50
python -m social_media_auto_poster.main jobs --status FAILED    # one status only
python -m social_media_auto_poster.main jobs --id 8a61f3de      # every detail of one job (an ID prefix is enough)
```

Shows job ID, time (UTC), post name, mode (`simulated` or `real`), status, and any errors; `--id` adds both SHA-256 hashes, file names, post ID, and where the files went. It never creates or changes the database. Exit code 0 on success; 1 if the database cannot be read or the `--id` prefix matches no job or more than one.

## Manual testing

### Interactive tester: `backend/scripts/app.py`

```powershell
cd backend
python scripts\app.py                                 # sandbox: backend\sandbox (ignored by Git)
python scripts\app.py --sandbox D:\tmp\my-sandbox     # any other folder
```

A terminal menu to add sample posts (valid JPEG/PNG, invalid kinds, lone files, ambiguous), run, scan, look at the folders, see job history (via the `jobs` command), simulate a crash during publish (to watch startup recovery) or right after it (menu 13, to watch a job resumed from its checkpoint), resolve a `NEEDS_REVIEW` job, and reset. Everything happens in a **sandbox** with its own `data\`, `database\app.db`, logs, and run lock; your real data and job database are never touched. It refuses to start if the sandbox is, contains, or is inside your real data, `database\`, or `logs\` folder, and it only uses a folder that is empty or already marked as a sandbox (`.autoposter-sandbox`), so Reset can never delete anything else. Publishing is always simulated; Run and Scan call the real `run` / `scan` commands. Valid sample posts share the same tiny image, so adding the same caption twice demonstrates a `DUPLICATE`.

## Backups

The job database, `backend\database\app.db`, is the only record of what was published and the only protection against posting the same content twice. Back it up regularly, and always before a real run:

```powershell
Copy-Item backend\database\app.db "backend\database\app.db.backup-$(Get-Date -Format yyyyMMdd-HHmm)"
```

Copy it while no `run` is in progress. To restore, make sure no `run` is in progress, then copy the backup back over `backend\database\app.db`. If the database is lost while `archive\` still holds published posts, `run --real` refuses to start until it is restored. The checkpoint store (`langgraph_checkpoints.db`) does not need a backup: it only holds work interrupted by a crash.

## Configuration and security

Copy `backend\.env.example` to `backend\.env` for local configuration; the application reads `.env` only from `backend\`. `.env` is ignored by Git; `.env.example` contains only blank placeholders. Do not commit credentials, access tokens, local databases, content, logs, or the sandbox. OAuth and API access are not configured by this scaffold. Future integrations must use official APIs and comply with their permissions and policies.

`AUTOPOSTER_DATA_DIR` moves the content folder (default `backend\data`). If it points outside `backend\`, `scan` and `run` print a warning with the folder they use, and `run --real` refuses to start if that folder does not already have its `LinkedIn\incoming`, `archive`, and `failed` folders (a real run never creates them; run once without `--real` on a new setup). `database\`, `logs\`, and the run lock always stay in the base folder, `backend\`.

Job records live in `backend\database\app.db` (SQLite, created by `run`; ignored by Git). Graph checkpoints live in a separate file, `backend\database\langgraph_checkpoints.db`; it only ever holds jobs that were interrupted, and never caption text. A publishing request with an unknown outcome must not be retried automatically without a platform-safe way to establish whether it succeeded. Content should be archived only after success is confirmed.

## Architecture decisions

- **LangGraph is required** for orchestrating each post's workflow as an explicit, deterministic state graph. It does not use LLMs or autonomous agents; filesystem, validation, database, and API work live in ordinary Python modules that the graph's nodes call.
- **SQLite first.** The application database (business records) and the LangGraph checkpoint store are kept logically separate and introduced in separate milestones. PostgreSQL is deferred until there is a concrete need.
- **Web UI later.** A React frontend (`frontend/`) and a FastAPI API inside the Python package are planned, not built. The API will reuse the same functions as the CLI (`scan`, `run`, `jobs`, `resolve`) with no second storage layer, bind to `127.0.0.1` by default, and follow the same real-LinkedIn safety rules as the CLI (decision D32).

See `CLAUDE.md` for the binding project rules.

## Development milestones

0. Inspect the scaffold, plan, and establish `CLAUDE.md` (done).
1. Local simulation: typed LangGraph state, initial deterministic graph, pairing and validation, simulated publishing, tests.
2. Durable application records: SQLite repository, job states, hashes, duplicate detection, restart reconciliation.
3. Persistent LangGraph execution: SQLite checkpointer, resume and interruption tests (implemented; awaiting review).
4. Real LinkedIn publishing through official APIs, with safeguards for ambiguous outcomes.
5. Reliability, operational docs, PostgreSQL evaluation, and Facebook/Instagram planning.

No live publishing, OAuth, or file-processing feature should be considered available until implemented and covered by appropriate tests.
