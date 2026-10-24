# Social Media Auto-Poster

A local, folder-driven Python application scaffold for preparing and publishing social media content. The project is being built incrementally, starting with LinkedIn and adding other platforms only after their integrations are separately planned and validated.

## MVP goals

- Discover content placed in platform-specific incoming folders.
- Pair image files with UTF-8 caption files and validate them before processing.
- Track post state and processing history so interrupted work can be recovered safely.
- Publish through official platform APIs, archive only confirmed successes, and retain failures with useful diagnostics.
- Prevent duplicate processing and avoid blindly retrying requests whose publishing outcome is ambiguous.

## Non-goals for this scaffold

There is no working folder watcher, content validator, database/repository, scheduler, OAuth flow, or live publishing integration yet. The simulator does not contact a platform and its result is not confirmation of publication. This scaffold does not claim API access is configured.

## Current status

**Scaffold only (Milestone 1, Slices 0–1 complete).** What exists today:

- Packaging (`pyproject.toml`) with declared dependencies.
- An environment-variable template (`.env.example`).
- A platform-neutral publisher protocol (`platforms/base.py`).
- A safe simulation publisher that never contacts any platform (`platforms/simulator.py`).
- One test module (`tests/unit/test_layout.py`) that checks the package layout imports correctly.

Nothing else under `src/` has code yet: no LangGraph workflow, ingestion, or storage.

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
  platforms/                       Publisher protocol (base.py) and simulator (simulator.py)
  platforms/{linkedin,facebook,instagram}/   Empty
  graph/                           Empty; LangGraph workflow (Slice 5)
  ingestion/                       Empty; scanning and pairing (Slice 3)
  services/                        Empty; hashing and archiving helpers
  storage/                         Empty; SQLite repository (Milestone 2)
tests/unit/                        Layout import tests
scripts/, docs/                    Empty
logs/                              Local application logs; ignored by Git
```

`config.py` and `models.py` arrive in Slice 2 and `main.py` in Slice 6. The full target layout is in `CLAUDE.md` ("Target code layout").

## Content naming convention

The initial convention is one image and one UTF-8 text caption with the same filename stem, for example:

```text
data/LinkedIn/incoming/product-launch.jpg
data/LinkedIn/incoming/product-launch.txt
```

The planned initial image extensions are `.jpg`, `.jpeg`, and `.png`. Pairing, extension validation, and file-copy stability checks are not implemented yet. This convention is provisional; media support will be reviewed against each platform's official API before implementation.

## Windows setup

Use Python 3.12 or newer; **Python 3.13 is the recommended version**. From PowerShell in the project root:

```powershell
py install 3.13                      # once, if Python 3.13 is not installed (Python Install Manager)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
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

`pytest` will report that no tests were collected until Milestone 1 adds them.

There is no runnable application entry point yet. Once `main.py` is implemented, the intended invocation is:

```powershell
python -m social_media_auto_poster.main
```

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
