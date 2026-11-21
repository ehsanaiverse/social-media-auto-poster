# Social Media Auto-Poster: backend

The Python package `social_media_auto_poster`: folder scanning, validation, the LangGraph workflow, the SQLite job database, and the command-line interface. The project overview is in the [root README](../README.md)

Publishing is **simulated** by default: nothing contacts LinkedIn or any other platform unless you run `run --real` (or confirm a real run in the dashboard). See "Real mode" in the root README.

## Setup (Windows PowerShell)

Python 3.12 or newer; the project is developed on Python 3.14. The virtual environment `.vevn` lives at the repo root.

```powershell
py -3.14 -m venv .vevn               # once, from the repo root
.\.vevn\Scripts\Activate.ps1
cd backend
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Or, from `requirements.txt` (the same direct dependencies): `python -m pip install -r requirements.txt` then `python -m pip install -e . --no-deps`.

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
python -m social_media_auto_poster.main linkedin-check      # check the LinkedIn token and setup; never posts
python -m social_media_auto_poster.main run --real          # publish to LinkedIn (token from backend\.env)
python -m social_media_auto_poster.main jobs                # read-only job history
python -m social_media_auto_poster.main resolve <id> --failed   # settle a NEEDS_REVIEW job (or --published <post_id>)
```

Each command has `--help`. Exit codes and options are described in the root README and in `CLAUDE.md`.

## Preparing a Reel video

A phone video often lacks two things Instagram requires: the `moov` atom at the front (faststart) and no edit lists. `scripts\prepare-reel.ps1` re-muxes it with ffmpeg into `data\Instagram\incoming\<Name>.mp4`; the source video is never changed or deleted.

```powershell
.\scripts\prepare-reel.ps1 -Source "C:\Users\me\Downloads\clip.mp4" -Name reel2
```

Then write `data\Instagram\incoming\reel2.txt` and run `python -m social_media_auto_poster.main run --platform instagram` (or add it in the dashboard). ffmpeg is a manual prerequisite for this developer script only: install it with `winget install Gyan.FFmpeg` and reopen the terminal. It is not a dependency of the application.

## Local API

The dashboard in `frontend/` talks to a small local API, in `backend/app/`. It uses the same functions as the commands above, so a run from the API is the same run. It is optional: the commands work without it.

```powershell
python -m pip install -e ".[dev,api]"   # once: adds FastAPI, uvicorn and python-multipart
python -m app                           # http://127.0.0.1:8000 (Ctrl+C to stop)
```

Open `http://127.0.0.1:8000/docs` for the list of endpoints. Runs are simulated unless a request says `"real": true` **and** `"confirm": "publish to LinkedIn"`. Real runs have the same safeguards as `run --real`, and they need the same run lock as the commands (a second run gets `409`).

### Dashboard

With the API running, start the dashboard from a second PowerShell window:

```powershell
cd D:\social-media-auto-poster\frontend
npm ci          # once, and after package-lock.json changes
npm run dev     # http://localhost:5173
npm test        # frontend tests (Vitest); also npm run typecheck and npm run build
```

To try the dashboard without touching your real posts, database or token, start the API with its own base folder: `$env:AUTOPOSTER_BASE_DIR = "D:\social-media-auto-poster\backend\sandbox\ui"` in the API's window, then one `python -m social_media_auto_poster.main run` there (it creates the sandbox's folders), then `python -m app`. That folder has no `.env`, so nothing can be posted for real from it.

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
