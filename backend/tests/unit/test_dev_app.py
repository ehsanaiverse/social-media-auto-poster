"""Tests for the interactive manual tester (scripts/app.py). Temporary folders only."""

import importlib.util
import os
from pathlib import Path

import pytest

from social_media_auto_poster.config import BASE_DIR_VAR, DATA_DIR_VAR
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "app.py"
_spec = importlib.util.spec_from_file_location("dev_app", SCRIPT)
dev_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev_app)


def candidate(incoming: Path, stem: str, ext: str) -> PostCandidate:
    return PostCandidate(Platform.LINKEDIN, stem, incoming / f"{stem}{ext}", incoming / f"{stem}.txt")


# Sandbox safety


@pytest.fixture
def layout(tmp_path):
    repo = tmp_path / "repo"
    real_data = repo / "data"
    real_data.mkdir(parents=True)
    return repo, real_data


@pytest.mark.parametrize(
    "where",
    ["repo", "real-data", "inside-real-data", "contains-real-data"],
)
def test_unsafe_sandbox_locations_are_refused(layout, where):
    repo, real_data = layout
    sandbox = {
        "repo": repo,
        "real-data": real_data,
        "inside-real-data": real_data / "LinkedIn" / "sandbox",
        "contains-real-data": repo.parent,
    }[where]

    with pytest.raises(dev_app.SandboxError):
        dev_app.check_sandbox(sandbox, repo_root=repo, real_data=real_data)


@pytest.mark.parametrize("where", ["repo/sandbox", "elsewhere"])
def test_separate_sandbox_is_accepted(layout, tmp_path, where):
    repo, real_data = layout
    sandbox = repo / "sandbox" if where == "repo/sandbox" else tmp_path / "elsewhere"

    assert dev_app.check_sandbox(sandbox, repo_root=repo, real_data=real_data) == sandbox.resolve()


def test_reset_refuses_folder_without_marker(tmp_path):
    folder = tmp_path / "not-a-sandbox"
    folder.mkdir()
    (folder / "precious.txt").write_text("keep me")

    with pytest.raises(dev_app.SandboxError, match="marker"):
        dev_app.Sandbox(folder).reset()
    assert (folder / "precious.txt").exists()


def test_reset_clears_only_the_sandbox(tmp_path):
    sandbox = dev_app.Sandbox(tmp_path / "sandbox")
    sandbox.prepare()
    dev_app.add_valid_post(sandbox.incoming, "launch", "Hello")
    neighbour = tmp_path / "neighbour.txt"
    neighbour.write_text("untouched")

    sandbox.reset()

    assert list(sandbox.incoming.iterdir()) == []
    assert (sandbox.root / dev_app.MARKER_NAME).exists()
    assert neighbour.exists()


# Sample files


@pytest.mark.parametrize(("image", "ext"), [("jpg", ".jpg"), ("png", ".png")])
def test_valid_samples_pass_validation(tmp_path, image, ext):
    dev_app.add_valid_post(tmp_path, "launch", "Hello LinkedIn", image)

    assert validate_candidate(candidate(tmp_path, "launch", ext)).is_valid


def test_sample_images_are_real_files():
    jpeg, png = dev_app.JPEG_1X1, dev_app.png_1x1()

    assert jpeg.startswith(b"\xff\xd8\xff") and jpeg.endswith(b"\xff\xd9")
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")


@pytest.mark.parametrize(
    ("kind", "ext", "expected"),
    [
        ("gif", ".gif", "unsupported image type '.gif' (allowed: .jpg, .jpeg, .png)"),
        ("empty-caption", ".jpg", "caption is empty"),
        ("renamed-text", ".jpg", "image content is not a valid JPEG"),
    ],
)
def test_invalid_samples_fail_with_expected_error(tmp_path, kind, ext, expected):
    dev_app.add_invalid_post(tmp_path, "bad", kind)

    assert validate_candidate(candidate(tmp_path, "bad", ext)).errors == (expected,)


def test_lone_files_are_pending_and_ambiguous_post_is_ambiguous(tmp_path):
    dev_app.add_lone_image(tmp_path, "lonely")
    dev_app.add_lone_caption(tmp_path, "waiting")
    dev_app.add_ambiguous_post(tmp_path, "banner")

    result = scan_incoming(tmp_path, Platform.LINKEDIN)

    assert result.candidates == ()
    assert [p.name for p in result.pending] == ["lonely.jpg", "waiting.txt"]
    assert [[p.name for p in group] for group in result.ambiguous] == [["banner.jpg", "banner.png", "banner.txt"]]


def test_existing_files_are_never_overwritten(tmp_path):
    dev_app.add_valid_post(tmp_path, "launch", "First")

    with pytest.raises(FileExistsError):
        dev_app.add_valid_post(tmp_path, "launch", "Second")
    assert (tmp_path / "launch.txt").read_text(encoding="utf-8") == "First"


@pytest.mark.parametrize("stem", ["", "  ", "a/b", "a:b", ".hidden", "~$lock"])
def test_bad_post_names_are_rejected(tmp_path, stem):
    with pytest.raises(ValueError):
        dev_app.add_valid_post(tmp_path, stem, "Hello")
    assert list(tmp_path.iterdir()) == []


# Isolation from the real project


def test_run_writes_only_inside_the_sandbox(layout, monkeypatch, capsys):
    repo, _ = layout
    monkeypatch.chdir(repo)
    monkeypatch.setenv(BASE_DIR_VAR, str(repo))
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    sandbox = dev_app.Sandbox(repo / "sandbox")
    sandbox.prepare()
    dev_app.add_valid_post(sandbox.incoming, "launch", "Hello")

    assert sandbox.run() == 0

    assert "SIMULATED (not published)" in capsys.readouterr().out
    assert (sandbox.root / "database" / "app.db").exists()
    assert (sandbox.root / "logs" / "autoposter.log").exists()
    assert not (repo / "database").exists()
    assert not (repo / "logs").exists()
    assert not (repo / "autoposter.lock").exists()
    assert Path.cwd() == repo  # Unchanged.
    assert os.environ[BASE_DIR_VAR] == str(repo)  # Restored.
    assert DATA_DIR_VAR not in os.environ  # Restored.
    [record] = sandbox.history()
    assert (record.stem, record.status) == ("launch", JobStatus.SIMULATED)


def test_history_is_empty_before_any_run_and_creates_no_database(tmp_path):
    sandbox = dev_app.Sandbox(tmp_path / "sandbox")
    sandbox.prepare()

    assert sandbox.history() == []
    assert not (sandbox.root / "database").exists()


def test_folder_contents_lists_each_folder(tmp_path):
    sandbox = dev_app.Sandbox(tmp_path / "sandbox")
    sandbox.prepare()
    dev_app.add_lone_image(sandbox.incoming, "lonely")

    assert sandbox.folder_contents() == {"incoming": ["lonely.jpg"], "failed": [], "archive": []}


# Crash simulation


def test_simulated_crash_is_recovered_by_the_next_run(layout, monkeypatch, capsys):
    repo, _ = layout
    monkeypatch.chdir(repo)
    monkeypatch.setenv(BASE_DIR_VAR, str(repo))
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    sandbox = dev_app.Sandbox(repo / "sandbox")
    sandbox.prepare()
    dev_app.add_valid_post(sandbox.incoming, "launch", "Hello")

    job_id = sandbox.simulate_crash("launch", PublishMode.SIMULATED)
    assert [(r.job_id, r.status) for r in sandbox.history()] == [(job_id, JobStatus.PUBLISHING)]

    assert sandbox.run() == 0

    out = capsys.readouterr().out
    assert "RECOVERED (1)" in out
    assert "SIMULATED (not published)" in out


def test_simulate_crash_needs_a_complete_post(tmp_path):
    sandbox = dev_app.Sandbox(tmp_path / "sandbox")
    sandbox.prepare()
    dev_app.add_lone_image(sandbox.incoming, "lonely")

    with pytest.raises(ValueError, match="No complete post"):
        sandbox.simulate_crash("lonely", PublishMode.REAL)


def test_history_menu_uses_the_jobs_command(layout, monkeypatch, capsys):
    repo, _ = layout
    monkeypatch.chdir(repo)
    monkeypatch.setenv(BASE_DIR_VAR, str(repo))
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    sandbox = dev_app.Sandbox(repo / "sandbox")
    sandbox.prepare()
    dev_app.add_valid_post(sandbox.incoming, "launch", "Hello")
    sandbox.run()
    capsys.readouterr()

    assert sandbox.jobs() == 0

    out = capsys.readouterr().out
    assert "STATUS" in out and "launch" in out and "SIMULATED" in out
    assert not (repo / "database").exists()


# Stricter sandbox rules (real database/logs folders, non-empty folders)


@pytest.mark.parametrize(
    "where", ["database", "database/inner", "logs", "logs/inner"],
)
def test_real_database_and_logs_folders_are_refused(layout, where):
    repo, real_data = layout

    with pytest.raises(dev_app.SandboxError, match="real (job database|logs) folder"):
        dev_app.check_sandbox(repo / where, repo_root=repo, real_data=real_data)


def test_non_empty_folder_without_marker_is_never_adopted(layout):
    repo, _ = layout
    src = repo / "src"
    src.mkdir()
    (src / "module.py").write_text("print('precious')")

    with pytest.raises(dev_app.SandboxError, match="not empty and is not a sandbox"):
        dev_app.Sandbox(src).prepare()
    assert not (src / dev_app.MARKER_NAME).exists()
    assert (src / "module.py").exists()


def test_existing_marked_sandbox_is_reused(tmp_path):
    sandbox = dev_app.Sandbox(tmp_path / "sandbox")
    sandbox.prepare()
    dev_app.add_lone_image(sandbox.incoming, "lonely")

    dev_app.Sandbox(tmp_path / "sandbox").prepare()  # Not empty, but marked: fine.

    assert (sandbox.incoming / "lonely.jpg").exists()


def test_main_refuses_to_start_in_a_non_empty_unmarked_folder(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "my-documents"
    folder.mkdir()
    (folder / "thesis.docx").write_bytes(b"precious")
    monkeypatch.setattr(dev_app, "real_data_dir", lambda: tmp_path / "real-data")

    assert dev_app.main(["--sandbox", str(folder)]) == 1

    assert "Refusing to start" in capsys.readouterr().err
    assert sorted(p.name for p in folder.iterdir()) == ["thesis.docx"]


# Resolve through the tester


def test_resolve_a_needs_review_job_through_the_tester(layout, monkeypatch, capsys):
    repo, _ = layout
    monkeypatch.chdir(repo)
    monkeypatch.setenv(BASE_DIR_VAR, str(repo))
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    sandbox = dev_app.Sandbox(repo / "sandbox")
    sandbox.prepare()
    dev_app.add_valid_post(sandbox.incoming, "launch", "Hello")
    job_id = sandbox.simulate_crash("launch", PublishMode.REAL)
    sandbox.run()  # Recovery: NEEDS_REVIEW, files moved to failed/.
    assert [r.job_id for r in sandbox.needs_review()] == [job_id]
    capsys.readouterr()

    assert sandbox.resolve(job_id[:8], "--failed") == 0

    assert "Resolved job" in capsys.readouterr().out
    assert sandbox.needs_review() == []
    assert [r.status for r in sandbox.history()] == [JobStatus.FAILED]
    assert not (repo / "database").exists()
