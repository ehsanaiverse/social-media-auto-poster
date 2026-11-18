"""LinkedIn credentials from backend/.env, and token expiry."""

from datetime import date

import pytest

from social_media_auto_poster.platforms.linkedin.credentials import (
    CredentialsError,
    LinkedInCredentials,
    TokenState,
    load_linkedin_credentials,
)

TODAY = date(2026, 10, 28)
TOKEN = "TEST-TOKEN-must-never-leak-7f3a"


def load(environ: dict) -> LinkedInCredentials:
    return load_linkedin_credentials(None, environ, today=TODAY)


def test_reads_token_and_creation_date_from_the_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        f"LINKEDIN_ACCESS_TOKEN={TOKEN}\nLINKEDIN_TOKEN_CREATED=2026-10-01\n", encoding="utf-8"
    )

    credentials = load_linkedin_credentials(tmp_path, today=TODAY)

    assert credentials.access_token == TOKEN
    assert credentials.token_created == date(2026, 10, 1)
    assert credentials.expires_on == date(2026, 11, 30)  # 60 days later.


def test_the_real_environment_overrides_the_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LINKEDIN_ACCESS_TOKEN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "from-environment")
    assert load_linkedin_credentials(tmp_path, today=TODAY).access_token == "from-environment"


@pytest.mark.parametrize(("environ", "message"), [
    ({}, "LINKEDIN_ACCESS_TOKEN is not set in backend\\.env"),
    ({"LINKEDIN_ACCESS_TOKEN": "   "}, "LINKEDIN_ACCESS_TOKEN is not set"),
    ({"LINKEDIN_ACCESS_TOKEN": "two words"}, "contains spaces"),
    ({"LINKEDIN_ACCESS_TOKEN": TOKEN, "LINKEDIN_TOKEN_CREATED": "28/10/2026"}, "must be a date like 2026-10-28"),
    ({"LINKEDIN_ACCESS_TOKEN": TOKEN, "LINKEDIN_TOKEN_CREATED": "2026-10-29"}, "is in the future"),
])
def test_unusable_settings_are_refused(environ, message):
    with pytest.raises(CredentialsError, match=message.replace("\\", "\\\\")) as raised:
        load(environ)
    assert TOKEN not in str(raised.value)


def test_the_creation_date_is_optional():
    credentials = load({"LINKEDIN_ACCESS_TOKEN": TOKEN})
    assert credentials.token_created is None and credentials.expires_on is None
    assert credentials.state(TODAY) is TokenState.UNKNOWN
    assert "expiry cannot be checked" in credentials.describe(TODAY)


@pytest.mark.parametrize(("created", "state", "phrase"), [
    (date(2026, 10, 28), TokenState.VALID, "expires on 2026-12-27 (60 days left)"),
    (date(2026, 9, 6), TokenState.VALID, "(8 days left)"),
    (date(2026, 9, 5), TokenState.EXPIRING, "(7 day(s) left)"),  # Warned from 7 days before.
    (date(2026, 8, 30), TokenState.EXPIRING, "(1 day(s) left)"),
    (date(2026, 8, 29), TokenState.EXPIRED, "expired on 2026-10-28"),  # Day 60: no longer valid.
    (date(2026, 1, 1), TokenState.EXPIRED, "expired on 2026-03-02"),
])
def test_expiry(created, state, phrase):
    credentials = LinkedInCredentials(access_token=TOKEN, token_created=created)
    assert credentials.state(TODAY) is state
    assert phrase in credentials.describe(TODAY)
    if state is not TokenState.VALID:
        assert "LINKEDIN_ACCESS_TOKEN" in credentials.describe(TODAY)  # Says what to renew.


def test_the_token_is_hidden_from_repr():
    assert TOKEN not in repr(LinkedInCredentials(access_token=TOKEN, token_created=TODAY))
