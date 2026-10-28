"""A data folder outside backend/ is warned about; a real run never creates the data folder.

Found during the first real post: AUTOPOSTER_DATA_DIR in backend/.env still pointed at
the old repo-root data folder, and run --real silently created empty folders there.
"""

from pathlib import Path

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.config import Settings, data_dir_outside_base, ensure_data_dirs, missing_platform_dirs
from social_media_auto_poster.models import Platform, PublishResult

from linkedin_fake import JPEG

LINKEDIN = Platform.LINKEDIN


class FakePublisher:
    def __init__(self):
        self.calls = 0

    def publish(self, post, /):
        self.calls += 1
        return PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:1")


@pytest.fixture
def base(tmp_path, monkeypatch) -> Path:
    base = tmp_path / "backend"
    base.mkdir()
    monkeypatch.chdir(base)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(base))
    return base


@pytest.fixture
def publisher(monkeypatch) -> FakePublisher:
    fake = FakePublisher()
    requested = []
    monkeypatch.setattr(cli, "_real_publisher", lambda settings: requested.append(settings) or fake)
    fake.requested = requested
    return fake


def use_data_dir(monkeypatch, path: Path) -> Settings:
    monkeypatch.setenv("AUTOPOSTER_DATA_DIR", str(path))
    return cli.load_settings()


def add_post(settings: Settings) -> None:
    incoming = settings.incoming_dir(LINKEDIN)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text("Hello LinkedIn", encoding="utf-8")


OUTSIDE_WARNING = "Warning: the data folder is outside backend\\: {} (check AUTOPOSTER_DATA_DIR in backend\\.env)"


# The helpers


def test_outside_is_judged_on_the_resolved_path(tmp_path):
    base = tmp_path / "backend"
    assert not data_dir_outside_base(Settings(base_dir=base, data_dir=base / "data"))
    assert not data_dir_outside_base(Settings(base_dir=base, data_dir=base / "content" / "posts"))
    assert data_dir_outside_base(Settings(base_dir=base, data_dir=tmp_path / "data"))
    assert data_dir_outside_base(Settings(base_dir=base, data_dir=(base / ".." / "data").resolve()))


def test_missing_platform_dirs_lists_only_what_is_missing(tmp_path):
    settings = Settings(base_dir=tmp_path, data_dir=tmp_path / "data")
    assert [p.name for p in missing_platform_dirs(settings, LINKEDIN)] == ["incoming", "archive", "failed"]
    settings.incoming_dir(LINKEDIN).mkdir(parents=True)
    assert [p.name for p in missing_platform_dirs(settings, LINKEDIN)] == ["archive", "failed"]
    ensure_data_dirs(settings)
    assert missing_platform_dirs(settings, LINKEDIN) == []


# Warnings


def test_scan_warns_with_the_resolved_path(base, tmp_path, monkeypatch, capsys):
    settings = use_data_dir(monkeypatch, tmp_path / "old-root" / "data")
    ensure_data_dirs(settings)

    assert cli.main(["scan", "--interval", "0"]) == 0

    assert OUTSIDE_WARNING.format(settings.data_dir) in capsys.readouterr().err


def test_scan_of_an_explicit_folder_does_not_warn(base, tmp_path, monkeypatch, capsys):
    use_data_dir(monkeypatch, tmp_path / "old-root" / "data")
    other = tmp_path / "somewhere"
    other.mkdir()

    assert cli.main(["scan", "--dir", str(other), "--interval", "0"]) == 0

    assert "outside backend" not in capsys.readouterr().err


def test_a_simulated_run_warns_but_still_runs(base, tmp_path, monkeypatch, capsys):
    settings = use_data_dir(monkeypatch, tmp_path / "old-root" / "data")

    assert cli.main(["run", "--interval", "0"]) == 0

    err = capsys.readouterr().err
    assert OUTSIDE_WARNING.format(settings.data_dir).removeprefix("Warning: ") in err
    assert missing_platform_dirs(settings, LINKEDIN) == []  # A simulated run still creates the folders.
    log = (base / "logs" / "autoposter.log").read_text(encoding="utf-8")
    assert "the data folder is outside backend" in log


def test_the_default_data_folder_gives_no_warning(base, monkeypatch, capsys):
    monkeypatch.delenv("AUTOPOSTER_DATA_DIR", raising=False)

    assert cli.main(["run", "--interval", "0"]) == 0
    assert cli.main(["scan", "--interval", "0"]) == 0

    assert "outside backend" not in capsys.readouterr().err


# A real run never creates the data folder


@pytest.mark.parametrize("where", ["outside", "inside"])
def test_a_real_run_refuses_a_missing_data_folder_and_creates_nothing(base, tmp_path, monkeypatch, capsys, publisher, where):
    data = tmp_path / "old-root" / "data" if where == "outside" else base / "data"
    use_data_dir(monkeypatch, data)

    assert cli.main(["run", "--real", "--interval", "0"]) == 1

    err = capsys.readouterr().err
    assert f"REFUSED: the data folder {data} does not have its LinkedIn folders:" in err
    assert "Real runs never create them" in err and "Nothing was published." in err
    assert not data.exists()  # Nothing was created.
    assert publisher.requested == [] and publisher.calls == 0  # Refused before even asking for LinkedIn.


def test_a_real_run_refuses_when_only_some_folders_exist(base, monkeypatch, capsys, publisher):
    settings = use_data_dir(monkeypatch, base / "data")
    settings.incoming_dir(LINKEDIN).mkdir(parents=True)
    add_post(settings)

    assert cli.main(["run", "--real", "--interval", "0"]) == 1

    err = capsys.readouterr().err
    assert str(settings.archive_dir(LINKEDIN)) in err and str(settings.failed_dir(LINKEDIN)) in err
    assert str(settings.incoming_dir(LINKEDIN)) not in err.split("LinkedIn folders:")[1].split("Real runs")[0]
    assert not settings.archive_dir(LINKEDIN).exists()
    assert publisher.calls == 0


def test_a_real_run_with_an_existing_outside_folder_warns_and_publishes(base, tmp_path, monkeypatch, capsys, publisher):
    settings = use_data_dir(monkeypatch, tmp_path / "shared" / "data")
    ensure_data_dirs(settings)
    add_post(settings)

    assert cli.main(["run", "--real", "--interval", "0"]) == 0

    assert "the data folder is outside backend" in capsys.readouterr().err
    assert publisher.calls == 1
