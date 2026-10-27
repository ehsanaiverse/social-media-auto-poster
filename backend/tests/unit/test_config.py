"""Tests for settings loading and content-folder creation, using temporary directories only."""

from pathlib import Path

import pytest

from social_media_auto_poster import config
from social_media_auto_poster.config import (
    BASE_DIR_VAR,
    DATA_DIR_VAR,
    ProjectDirNotFoundError,
    ensure_data_dirs,
    load_settings,
)
from social_media_auto_poster.models import Platform

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_data_dir_defaults_to_data_under_base_dir(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={})
    assert settings.data_dir == tmp_path.resolve() / "data"


def test_blank_data_dir_value_uses_default(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={DATA_DIR_VAR: ""})
    assert settings.data_dir == tmp_path.resolve() / "data"


def test_relative_data_dir_is_resolved_against_base_dir(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={DATA_DIR_VAR: "content"})
    assert settings.data_dir == tmp_path.resolve() / "content"


def test_absolute_data_dir_is_used_as_is(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    settings = load_settings(base_dir=tmp_path / "base", environ={DATA_DIR_VAR: str(elsewhere)})
    assert settings.data_dir == elsewhere.resolve()


def test_env_file_is_read_when_environ_not_given(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    (tmp_path / ".env").write_text(f"{DATA_DIR_VAR}=from-dotenv\n", encoding="utf-8")
    settings = load_settings(base_dir=tmp_path)
    assert settings.data_dir == tmp_path.resolve() / "from-dotenv"


def test_real_environment_overrides_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_DIR_VAR, "from-environment")
    (tmp_path / ".env").write_text(f"{DATA_DIR_VAR}=from-dotenv\n", encoding="utf-8")
    settings = load_settings(base_dir=tmp_path)
    assert settings.data_dir == tmp_path.resolve() / "from-environment"


def test_missing_env_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    settings = load_settings(base_dir=tmp_path)
    assert settings.data_dir == tmp_path.resolve() / "data"


def test_platform_folder_helpers(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={})
    root = tmp_path.resolve() / "data" / "LinkedIn"
    assert settings.incoming_dir(Platform.LINKEDIN) == root / "incoming"
    assert settings.archive_dir(Platform.LINKEDIN) == root / "archive"
    assert settings.failed_dir(Platform.LINKEDIN) == root / "failed"


def test_ensure_data_dirs_creates_all_platform_folders(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={})
    ensure_data_dirs(settings)
    for platform in Platform:
        assert settings.incoming_dir(platform).is_dir()
        assert settings.archive_dir(platform).is_dir()
        assert settings.failed_dir(platform).is_dir()


def test_ensure_data_dirs_is_idempotent_and_keeps_files(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={})
    ensure_data_dirs(settings)
    existing = settings.incoming_dir(Platform.LINKEDIN) / "post.txt"
    existing.write_text("caption", encoding="utf-8")

    ensure_data_dirs(settings)

    assert existing.read_text(encoding="utf-8") == "caption"


def test_log_and_lock_files_live_under_base_dir(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={DATA_DIR_VAR: "elsewhere"})
    assert settings.log_file == tmp_path.resolve() / "logs" / "autoposter.log"
    assert settings.lock_file == tmp_path.resolve() / "autoposter.lock"


def test_app_db_lives_under_base_dir(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={DATA_DIR_VAR: "elsewhere"})
    assert settings.app_db_path == tmp_path.resolve() / "database" / "app.db"


# Base folder


def test_default_base_dir_is_backend_whatever_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(environ={})  # No AUTOPOSTER_BASE_DIR.
    assert BACKEND_DIR.name == "backend" and (BACKEND_DIR / "pyproject.toml").is_file()
    assert settings.base_dir == BACKEND_DIR
    assert settings.data_dir == BACKEND_DIR / "data"
    assert settings.app_db_path == BACKEND_DIR / "database" / "app.db"
    assert settings.log_file == BACKEND_DIR / "logs" / "autoposter.log"
    assert settings.lock_file == BACKEND_DIR / "autoposter.lock"
    assert not (tmp_path / "data").exists()  # Loading settings creates nothing.


def test_base_dir_variable_moves_every_runtime_path(tmp_path):
    settings = load_settings(environ={BASE_DIR_VAR: str(tmp_path)})
    assert settings.base_dir == tmp_path.resolve()
    assert settings.data_dir == tmp_path.resolve() / "data"
    assert settings.app_db_path == tmp_path.resolve() / "database" / "app.db"
    assert settings.lock_file == tmp_path.resolve() / "autoposter.lock"


def test_env_file_is_read_from_the_base_folder_not_the_current_folder(tmp_path, monkeypatch):
    base, elsewhere = tmp_path / "base", tmp_path / "elsewhere"
    base.mkdir()
    elsewhere.mkdir()
    (base / ".env").write_text(f"{DATA_DIR_VAR}=from-base\n", encoding="utf-8")
    (elsewhere / ".env").write_text(f"{DATA_DIR_VAR}=from-current-folder\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv(BASE_DIR_VAR, str(base))
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)

    assert load_settings().data_dir == base.resolve() / "from-base"


def test_base_dir_is_not_read_from_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv(BASE_DIR_VAR, str(tmp_path))
    monkeypatch.delenv(DATA_DIR_VAR, raising=False)
    (tmp_path / ".env").write_text(f"{BASE_DIR_VAR}={tmp_path / 'other'}\n", encoding="utf-8")
    assert load_settings().base_dir == tmp_path.resolve()


def test_missing_backend_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)  # No pyproject.toml here.
    with pytest.raises(ProjectDirNotFoundError, match="Cannot find the backend folder"):
        load_settings(environ={})
