"""Tests for settings loading and content-folder creation, using temporary directories only."""

from social_media_auto_poster.config import (
    DATA_DIR_VAR,
    LINKEDIN_CLIENT_ID_VAR,
    LINKEDIN_CLIENT_SECRET_VAR,
    LINKEDIN_REDIRECT_URI_VAR,
    ensure_data_dirs,
    load_settings,
)
from social_media_auto_poster.models import Platform


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


# Fake, isolated values only; never real credentials.
FAKE_SECRET = "fake-secret-for-tests"


def test_linkedin_settings_default_to_none(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={})
    assert settings.linkedin_client_id is None
    assert settings.linkedin_client_secret is None
    assert settings.linkedin_redirect_uri is None


def test_blank_linkedin_values_are_none(tmp_path):
    environ = {
        LINKEDIN_CLIENT_ID_VAR: "",
        LINKEDIN_CLIENT_SECRET_VAR: "   ",
        LINKEDIN_REDIRECT_URI_VAR: "",
    }
    settings = load_settings(base_dir=tmp_path, environ=environ)
    assert settings.linkedin_client_id is None
    assert settings.linkedin_client_secret is None
    assert settings.linkedin_redirect_uri is None


def test_linkedin_settings_load_from_environ(tmp_path):
    environ = {
        LINKEDIN_CLIENT_ID_VAR: "fake-id",
        LINKEDIN_CLIENT_SECRET_VAR: FAKE_SECRET,
        LINKEDIN_REDIRECT_URI_VAR: "http://localhost:8000/callback",
    }
    settings = load_settings(base_dir=tmp_path, environ=environ)
    assert settings.linkedin_client_id == "fake-id"
    assert settings.linkedin_client_secret == FAKE_SECRET
    assert settings.linkedin_redirect_uri == "http://localhost:8000/callback"


def test_linkedin_settings_load_from_env_file(tmp_path, monkeypatch):
    for name in (LINKEDIN_CLIENT_ID_VAR, LINKEDIN_CLIENT_SECRET_VAR, LINKEDIN_REDIRECT_URI_VAR):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        f"{LINKEDIN_CLIENT_ID_VAR}=dotenv-id\n{LINKEDIN_CLIENT_SECRET_VAR}={FAKE_SECRET}\n",
        encoding="utf-8",
    )
    settings = load_settings(base_dir=tmp_path)
    assert settings.linkedin_client_id == "dotenv-id"
    assert settings.linkedin_client_secret == FAKE_SECRET
    assert settings.linkedin_redirect_uri is None


def test_real_environment_overrides_env_file_for_linkedin(tmp_path, monkeypatch):
    monkeypatch.setenv(LINKEDIN_CLIENT_ID_VAR, "env-id")
    (tmp_path / ".env").write_text(f"{LINKEDIN_CLIENT_ID_VAR}=dotenv-id\n", encoding="utf-8")
    settings = load_settings(base_dir=tmp_path)
    assert settings.linkedin_client_id == "env-id"


def test_loading_env_file_does_not_modify_os_environ(tmp_path, monkeypatch):
    import os

    monkeypatch.delenv(LINKEDIN_CLIENT_SECRET_VAR, raising=False)
    (tmp_path / ".env").write_text(f"{LINKEDIN_CLIENT_SECRET_VAR}={FAKE_SECRET}\n", encoding="utf-8")
    load_settings(base_dir=tmp_path)
    assert LINKEDIN_CLIENT_SECRET_VAR not in os.environ


def test_client_secret_is_not_in_repr_or_str(tmp_path):
    settings = load_settings(base_dir=tmp_path, environ={LINKEDIN_CLIENT_SECRET_VAR: FAKE_SECRET})
    assert FAKE_SECRET not in repr(settings)
    assert FAKE_SECRET not in str(settings)
