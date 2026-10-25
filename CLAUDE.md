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

## Reliability rules (non-negotiable)

- A filesystem event does not mean a file is fully copied. Check file stability (e.g. size/mtime unchanged across an interval, file openable) before processing.
- Deduplicate with stable job IDs and content hashes, not filenames alone. **Dedupe key (Milestone 2):** `platform` + `SHA-256(media bytes)` + `SHA-256(caption bytes)`, hashed separately and stored as separate values, never a hash of the raw bytes concatenated together.
- On startup, reconcile `incoming/` against stored job records so interrupted work is recovered.
- Job states are explicit. Current set: `DISCOVERED`, `VALIDATED`, `PUBLISHING`, `PUBLISHED`, `SIMULATED`, `FAILED`, `NEEDS_REVIEW` (refine as needed and document changes here).
- **`SIMULATED` is a terminal state.** A simulated run always ends in `SIMULATED` and never uses `PUBLISHED`. `PublishResult` carries `simulated: bool`, and `PUBLISHED` is only valid when `simulated` is `False` and the platform confirmed the post.
- Archive files **only** after a real, confirmed `PUBLISHED` result; the graph routes to the archive node only on that result. Failed or uncertain jobs keep their files (in `failed/` or `incoming/`) with an actionable error.
- **In Milestone 1, `SIMULATED` jobs leave their files in `incoming/`.** The archive service is still unit-tested directly against temporary directories.
- **Never blindly retry an external publish whose outcome is ambiguous** (timeout, lost response, crash while `PUBLISHING`). Mark it `NEEDS_REVIEW` unless the platform offers a safe way to check whether it succeeded.
- Use structured logging with job ID, platform, and state on every relevant log line.
- A simulated result must always be labelled as simulated. Never log, store, or display a simulated post as published to LinkedIn.

## Input conventions

- Folders: `data/<Platform>/{incoming,archive,failed}/` for `LinkedIn`, `Facebook`, `Instagram`.
- A post is one image plus one UTF-8 `.txt` caption with the same filename stem, e.g. `product-launch.jpg` + `product-launch.txt`.
- Supported images (initially): `.jpg`, `.jpeg`, `.png`. Video is out of scope for now.
- Handle explicitly: incomplete copies, missing caption, missing media, unsupported extension, empty or whitespace-only caption, undecodable caption.
- **Unpaired files are "pending", not errors.** A lone image or lone caption with no partner is left untouched: it does not become a job and is not moved to `failed/`. It is picked up on a later scan once its partner arrives.
- **Genuine validation errors fail immediately:** unsupported extension, empty or whitespace-only caption, caption that is not valid UTF-8.

## Security

- Never commit credentials, tokens, `.env`, databases, content in `data/`, or logs.
- Secrets come from environment variables (loaded from `.env` via `python-dotenv` locally). `.env.example` contains placeholder names only.
- Real LinkedIn integration must use official APIs, current documented permissions, and secure OAuth handling, verified against current official LinkedIn docs when Milestone 4 starts.

## Target code layout

```text
src/social_media_auto_poster/
├── config.py          # Settings loaded from env/.env; paths for data/ and database/
├── models.py          # Shared dataclasses/enums (JobStatus, PostCandidate, PublishResult)
├── graph/             # LangGraph only: state.py, nodes.py, workflow.py
├── ingestion/         # Folder scanning, file stability, media/caption pairing, validation
├── storage/           # SQLite repository layer (Milestone 2+)
├── platforms/         # base.py (publisher protocol), simulator, linkedin/ adapter
├── services/          # Hashing, archiving, other plain-Python helpers used by nodes
└── main.py            # CLI entry point
```

The folder structure was migrated in Milestone 1, Slice 1; `config.py` and `models.py` were added in Slice 2. `main.py` arrives in Slice 6, when it has real content.

Configuration: `AUTOPOSTER_DATA_DIR` sets the content root (default `data`, relative paths resolved against the base directory, which is the current working directory). Real environment variables override `.env`. `load_settings()` reads `.env` with `dotenv_values` and never mutates `os.environ`. Explain any significant structural change before making it, and do not add empty abstractions.

## Development method

Work **one milestone at a time**. Do not start the next milestone until the current one is tested, its results are explained, and the user has reviewed it.

| # | Milestone | Status |
| --- | --------- | ------ |
| 0 | Inspect, plan, establish this file | Done |
| 1 | Local simulation: package setup, typed state, initial deterministic graph, pairing/validation, simulated publish, tests | In progress: Slices 0 (docs/packaging), 1 (layout migration), 2 (models + config) done; Slice 3 (ingestion) next |
| 2 | Durable application records: SQLite schema + repository, job states, hashes, duplicate detection, restart reconciliation | Not started |
| 3 | Persistent LangGraph execution: SQLite checkpointer, resume/interrupt tests, checkpoint data kept separate | Not started |
| 4 | Real LinkedIn publishing: verify official API, auth, media upload, post creation, ambiguous-outcome safeguards; simulated and real modes clearly separated | Not started |
| 5 | Reliability and expansion: logging/monitoring, operational docs, evaluate PostgreSQL, plan Facebook/Instagram adapters | Not started |

Every milestone delivers: the files changed, the reasoning behind key decisions, exact Windows (PowerShell) commands to install, run, and test, the expected output, automated tests, and known limitations plus the next step.

Tests must cover, as the features appear: normal execution, validation failures, duplicate detection, and recovery/resume behaviour. Use `pytest` with temporary directories, never the real `data/` folders.

## Dependencies

- `pyproject.toml` is the source of truth for dependencies: runtime in `[project] dependencies`, test tooling in `[project.optional-dependencies] dev`. The supported install is `pip install -e ".[dev]"`, which reads only `pyproject.toml`.
- `requirements.txt` is kept by the developer's choice but is **not** used by the documented install. It currently lists unpinned packages beyond the agreed set (including `langchain`, which is excluded above); do not treat it as authoritative or install from it.
- Add a dependency only in the milestone that uses it. Verify the package name, current version, and Python compatibility on PyPI or the official docs before adding or pinning it. Bounds are `>=` the version actually installed and tested, `<` the next major version.
- Current: `langgraph`, `python-dotenv` (runtime); `pytest` (dev).
- Expected later: `watchdog` (when folder watching is added), `langgraph-checkpoint-sqlite` (M3), `requests` or similar (M4). `langchain` is **not** a dependency (see "No LLMs" above).
- **Python version:** standardise on **Python 3.12+, preferring 3.13** (`requires-python = ">=3.12"`). `langgraph` lists classifiers through 3.13; 3.14 is not a supported target.

## Commands (Windows PowerShell, from repo root)

```powershell
py install 3.13                      # once, if Python 3.13 is not installed (Python Install Manager)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
```

The application entry point (`python -m social_media_auto_poster.main`) does not exist until Milestone 1.
