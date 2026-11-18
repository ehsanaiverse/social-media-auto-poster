"""LinkedIn credentials from ``backend/.env``: the access token and the date it was created.

The token is made by hand in the LinkedIn Developer Portal and lasts 60 days
("all access tokens are issued with a 60-day lifespan"); it is not refreshed
automatically. ``LINKEDIN_TOKEN_CREATED`` (optional, YYYY-MM-DD) lets real runs and
``linkedin-check`` warn before it expires.

Real environment variables override ``.env``. The token is never part of a repr,
a message, or a log line.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values

TOKEN_VAR = "LINKEDIN_ACCESS_TOKEN"
CREATED_VAR = "LINKEDIN_TOKEN_CREATED"
TOKEN_LIFETIME = timedelta(days=60)
WARN_WITHIN = timedelta(days=7)
RENEW_HINT = (
    f"create a new token in the LinkedIn Developer Portal and update {TOKEN_VAR} and {CREATED_VAR} in backend\\.env"
)


class CredentialsError(Exception):
    """The LinkedIn settings in backend/.env are missing or unusable."""


class TokenState(StrEnum):
    VALID = "valid"
    EXPIRING = "expiring"  # Within WARN_WITHIN of its expiry.
    EXPIRED = "expired"
    UNKNOWN = "unknown"  # No creation date given.


@dataclass(frozen=True)
class LinkedInCredentials:
    access_token: str = field(repr=False)
    token_created: date | None

    @property
    def expires_on(self) -> date | None:
        return self.token_created + TOKEN_LIFETIME if self.token_created else None

    def state(self, today: date) -> TokenState:
        if self.expires_on is None:
            return TokenState.UNKNOWN
        if today >= self.expires_on:
            return TokenState.EXPIRED
        if self.expires_on - today <= WARN_WITHIN:
            return TokenState.EXPIRING
        return TokenState.VALID

    def describe(self, today: date) -> str:
        """One plain sentence about the token's expiry, for warnings and linkedin-check."""
        state = self.state(today)
        if state is TokenState.UNKNOWN:
            return f"{CREATED_VAR} is not set, so the token's expiry cannot be checked"
        days = (self.expires_on - today).days
        if state is TokenState.EXPIRED:
            return (f"the LinkedIn access token expired on {self.expires_on} (created {self.token_created}; "
                    f"tokens last 60 days): {RENEW_HINT}")
        if state is TokenState.EXPIRING:
            return f"the LinkedIn access token expires on {self.expires_on} ({days} day(s) left): {RENEW_HINT} soon"
        return f"the LinkedIn access token expires on {self.expires_on} ({days} days left)"


def load_linkedin_credentials(
    base_dir: Path, environ: Mapping[str, str | None] | None = None, *, today: date | None = None
) -> LinkedInCredentials:
    """Read the token and its creation date from ``base_dir/.env`` and the environment."""
    if environ is None:
        environ = {**dotenv_values(base_dir / ".env"), **os.environ}
    token = (environ.get(TOKEN_VAR) or "").strip()
    if not token:
        raise CredentialsError(f"{TOKEN_VAR} is not set in backend\\.env")
    if any(char.isspace() for char in token):
        raise CredentialsError(f"{TOKEN_VAR} in backend\\.env contains spaces; paste the token alone")

    created_text = (environ.get(CREATED_VAR) or "").strip()
    created = None
    if created_text:
        try:
            created = date.fromisoformat(created_text)
        except ValueError:
            raise CredentialsError(f"{CREATED_VAR} must be a date like 2026-10-28") from None
        if created > (today or date.today()):
            raise CredentialsError(f"{CREATED_VAR} ({created}) is in the future")
    return LinkedInCredentials(access_token=token, token_created=created)
