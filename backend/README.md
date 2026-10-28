# Social Media Auto-Poster: backend

The Python package `social_media_auto_poster`: folder scanning, validation, the LangGraph workflow, the SQLite job database, and the command-line interface. The project overview is in the [root README](../README.md).

Publishing is **simulated**: nothing contacts LinkedIn or any other platform.

## Setup (Windows PowerShell)

Python 3.12 or newer; the project is developed on Python 3.14. The virtual environment `.vevn` lives at the repo root.

```powershell
py -3.14 -m venv .vevn               # once, from the repo root
.\.vevn\Scripts\Activate.ps1
cd backend
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Install in editable mode (`-e`): the application finds its runtime folders next to this file.

## Runtime folders

Everything the application writes lives in `backend\`, whichever folder you start it from:

```text
backend\.env                         Local settings (copy .env.example; never commit)
backend\data\<Platform>\{incoming,archive,failed}\
backend\database\app.db              Job database
backend\database\langgraph_checkpoints.db   Graph checkpoints (interrupted jobs only)
backend\logs\autoposter.log          Run log
backend\autoposter.lock              Single-run lock
backend\sandbox\                     Interactive tester sandbox
```

All of them are ignored by Git. `AUTOPOSTER_DATA_DIR` in `backend\.env` moves only the content folder (`data\`).

## Run

```powershell
python -m social_media_auto_poster.main scan --interval 0   # read-only preview of data\LinkedIn\incoming
python -m social_media_auto_poster.main run                 # process every complete post once (simulated)
python -m social_media_auto_poster.main run --real          # publish to LinkedIn (Milestone 4; refused until set up)
python -m social_media_auto_poster.main jobs                # read-only job history
python -m social_media_auto_poster.main resolve <id> --failed   # settle a NEEDS_REVIEW job (or --published <post_id>)
```

Each command has `--help`. Exit codes and options are described in the root README and in `CLAUDE.md`.

## Test

```powershell
python -m pytest
```

Every test runs in its own temporary folder and never touches `backend\data\` or `backend\database\app.db`.

## Interactive tester

```powershell
python scripts\app.py                               # sandbox: backend\sandbox
python scripts\app.py --sandbox D:\tmp\my-sandbox   # any other empty folder
```

A menu to add sample posts, run, scan, view folders and job history, simulate a crash during or right after publish (menu 13 shows a job resumed from its checkpoint), and resolve a `NEEDS_REVIEW` job. It works only inside its sandbox, which has its own data, database, logs, and lock.
