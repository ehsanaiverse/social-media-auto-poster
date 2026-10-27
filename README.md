# AutoPoster Backend

FastAPI backend for the Social Media Auto-Poster, wrapping the existing LangGraph + SQLite logic behind a REST API.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

### Option 1: From the `backend/` directory

```powershell
cd backend
python -m app
```
or:
```powershell
cd backend
uvicorn app.main:app --reload --port 8000
```

### Option 2: From the project root

```powershell
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

## API Endpoints

- `GET /api/scan` - Scan the incoming folder
- `POST /api/run` - Process every complete post once
- `GET /api/jobs` - List recent job records
- `GET /api/jobs/{id}` - Get a single job record

## Documentation

Interactive API docs are available at `http://localhost:8000/docs`.