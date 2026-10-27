"""Tests of the local API in backend/app, against a temporary data folder and database.

Skipped when the optional ``api`` extra (FastAPI) is not installed. Publishing is always simulated.
"""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from social_media_auto_poster.config import load_settings  # noqa: E402
from social_media_auto_poster.services.run_lock import single_run_lock  # noqa: E402

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "data")
    return tmp_path


@pytest.fixture
def incoming(base) -> Path:
    folder = base / "data" / "LinkedIn" / "incoming"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def client(base) -> TestClient:
    return TestClient(create_app(stability_interval_s=0))


def add_post(folder: Path, stem: str, caption: bytes | None = None, media: bytes = JPEG_BYTES) -> None:
    (folder / f"{stem}.jpg").write_bytes(media)
    (folder / f"{stem}.txt").write_bytes(caption if caption is not None else f"Caption for {stem}".encode())


def test_creating_the_app_creates_nothing_on_disk(base):
    create_app(stability_interval_s=0)
    assert list(base.iterdir()) == []


def test_jobs_is_empty_and_creates_no_database_before_the_first_run(base, client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == []
    assert not (base / "database").exists()


def test_scan_is_read_only_and_reports_new_posts(base, incoming, client):
    add_post(incoming, "launch")
    (incoming / "lonely.jpg").write_bytes(JPEG_BYTES)

    body = client.get("/api/scan").json()

    [candidate] = body["candidates"]
    assert candidate["stem"] == "launch"
    assert candidate["stable"] and candidate["valid"]
    assert candidate["duplicate_of"] is None
    assert body["pending"] == ["lonely.jpg"]
    assert not (base / "database").exists()
    assert sorted(p.name for p in incoming.iterdir()) == ["launch.jpg", "launch.txt", "lonely.jpg"]


def test_scan_reports_a_missing_incoming_folder(base, client):
    assert client.get("/api/scan").status_code == 404


def test_run_simulates_records_the_job_and_leaves_files_in_incoming(base, incoming, client):
    add_post(incoming, "launch")

    response = client.post("/api/run")

    assert response.status_code == 200
    body = response.json()
    assert body["simulated"] is True and body["completed"] is True
    assert body["exit_code"] == 0
    [job_line] = body["jobs"]
    assert job_line["outcome"] == "simulated"
    assert "not published" in job_line["result"]
    assert (incoming / "launch.jpg").exists()

    [job] = client.get("/api/jobs").json()
    assert job["status"] == "SIMULATED"
    assert job["mode"] == "simulated"
    assert isinstance(job["job_id"], str) and len(job["job_id"]) == 32  # UUID hex.
    assert client.get(f"/api/jobs/{job['job_id']}").json()["stem"] == "launch"
    assert client.get(f"/api/jobs/{job['job_id'][:8]}").json()["job_id"] == job["job_id"]


def test_run_uses_the_cli_run_path_with_run_logging(base, incoming, client):
    add_post(incoming, "launch")
    client.post("/api/run")

    log = load_settings().log_file.read_text(encoding="utf-8")
    assert "Run started" in log and "Run finished" in log
    assert "Caption for launch" not in log  # Never log caption text.


def test_second_run_reports_a_duplicate_and_creates_no_new_job(base, incoming, client):
    add_post(incoming, "launch")
    client.post("/api/run")
    [first] = client.get("/api/jobs").json()

    body = client.post("/api/run").json()

    [job_line] = body["jobs"]
    assert job_line["outcome"] == "duplicates"
    assert first["job_id"][:8] in job_line["note"]
    assert len(client.get("/api/jobs").json()) == 1
    assert client.get("/api/scan").json()["candidates"][0]["duplicate_of"] == first["job_id"]


def test_invalid_post_fails_and_moves_to_failed(base, incoming, client):
    add_post(incoming, "blank", caption=b"   ")

    body = client.post("/api/run").json()

    assert body["exit_code"] == 3
    assert body["jobs"][0]["outcome"] == "failed"
    assert not (incoming / "blank.jpg").exists()
    assert client.get("/api/jobs", params={"status": "FAILED"}).json()[0]["stem"] == "blank"


def test_run_is_refused_while_another_run_holds_the_lock(base, incoming, client):
    add_post(incoming, "launch")
    settings = load_settings()
    settings.lock_file.parent.mkdir(parents=True, exist_ok=True)

    with single_run_lock(settings.lock_file):
        response = client.post("/api/run")

    assert response.status_code == 409
    assert not (base / "database").exists()
    assert (incoming / "launch.jpg").exists()


def test_unknown_job_is_404(base, incoming, client):
    add_post(incoming, "launch")
    client.post("/api/run")
    assert client.get("/api/jobs/ffffffff").status_code == 404


def test_cors_allows_only_the_local_frontend(client):
    allowed = client.get("/api/jobs", headers={"Origin": "http://localhost:3000"})
    other = client.get("/api/jobs", headers={"Origin": "https://example.com"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in other.headers


def test_data_folder_is_not_served(base, incoming, client):
    add_post(incoming, "launch")
    assert client.get("/media/LinkedIn/incoming/launch.txt").status_code == 404
