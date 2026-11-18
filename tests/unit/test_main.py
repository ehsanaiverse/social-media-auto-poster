"""Tests for the command-line entry point. Every test uses a temporary folder."""

from pathlib import Path

import pytest

from social_media_auto_poster import main as cli


@pytest.fixture(autouse=True)
def isolated_base(tmp_path, monkeypatch):
    """Run every test from an empty folder, so the real .env and database/app.db are never read."""
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.chdir(base)
    monkeypatch.delenv("AUTOPOSTER_DATA_DIR", raising=False)
    return base


def touch(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"content")


def snapshot(folder: Path) -> dict[str, tuple[int, bytes]]:
    return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in folder.iterdir() if p.is_file()}


def test_scan_reports_candidates_pending_and_ambiguous(tmp_path, capsys):
    touch(tmp_path, "launch.jpg", "launch.txt", "lonely.jpg", "banner.jpg", "banner.png", "banner.txt")

    exit_code = cli.main(["scan", "--dir", str(tmp_path), "--interval", "0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "CANDIDATES (1)" in out
    assert "launch.jpg + launch.txt" in out
    assert "stable" in out
    assert "PENDING (1)" in out
    assert "  lonely.jpg" in out
    assert "AMBIGUOUS (1)" in out
    assert "banner: banner.jpg, banner.png, banner.txt" in out
    assert "Dry run: nothing was moved or published." in out


def test_scan_defaults_to_configured_linkedin_incoming(tmp_path, monkeypatch, capsys):
    incoming = tmp_path / "content" / "LinkedIn" / "incoming"
    touch(incoming, "launch.jpg", "launch.txt")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", "content")

    exit_code = cli.main(["scan", "--interval", "0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert str(incoming.resolve()) in out
    assert "launch.jpg + launch.txt" in out


def test_scan_missing_folder_exits_1_with_actionable_error(tmp_path, capsys):
    exit_code = cli.main(["scan", "--dir", str(tmp_path / "missing"), "--interval", "0"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "incoming folder not found" in captured.err
    assert "AUTOPOSTER_DATA_DIR" in captured.err
    assert captured.out == ""


def test_scan_reports_which_file_is_not_stable(tmp_path, monkeypatch, capsys):
    touch(tmp_path, "launch.jpg", "launch.txt")
    checked = []

    def fake_is_file_stable(path, interval_s):
        checked.append(path.name)
        return path.suffix != ".txt"

    monkeypatch.setattr(cli, "is_file_stable", fake_is_file_stable)

    cli.main(["scan", "--dir", str(tmp_path), "--interval", "0"])

    assert sorted(checked) == ["launch.jpg", "launch.txt"]
    assert "NOT STABLE (caption changing or locked)" in capsys.readouterr().out


def test_scan_empty_folder_says_nothing_found(tmp_path, capsys):
    exit_code = cli.main(["scan", "--dir", str(tmp_path), "--interval", "0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Nothing found." in out


def test_scan_never_moves_or_changes_files(tmp_path):
    touch(tmp_path, "a.jpg", "a.txt", "lone.png", "x.jpg", "x.png")
    before = snapshot(tmp_path)

    cli.main(["scan", "--dir", str(tmp_path), "--interval", "0"])

    assert snapshot(tmp_path) == before


def test_no_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])

    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("interval", ["-1", "soon"])
def test_invalid_interval_is_a_usage_error(tmp_path, interval, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["scan", "--dir", str(tmp_path), "--interval", interval])

    assert exc.value.code == 2
    assert "--interval" in capsys.readouterr().err


def test_scan_shows_validation_result_per_candidate(tmp_path, capsys):
    (tmp_path / "good.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    (tmp_path / "good.txt").write_bytes(b"Hello")
    touch(tmp_path, "animated.gif", "animated.txt")

    cli.main(["scan", "--dir", str(tmp_path), "--interval", "0"])

    lines = capsys.readouterr().out.splitlines()
    good = next(line for line in lines if "good.jpg" in line)
    animated = next(line for line in lines if "animated.gif" in line)
    assert good.endswith("valid   new")
    assert "INVALID: unsupported image type '.gif'" in animated
