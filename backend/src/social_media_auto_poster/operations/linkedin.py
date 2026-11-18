"""LinkedIn: the token's status (``linkedin-check`` and the API), and the real publisher for ``run --real``.

Other modules call ``_real_publisher`` and ``_linkedin_client`` through this module
(``linkedin_ops._real_publisher(...)``), never by importing the names: tests replace them here
to keep every test away from the real LinkedIn.
"""

import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import date

from social_media_auto_poster.config import Settings
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations.common import LOGGER_NAME
from social_media_auto_poster.platforms.base import Publisher
from social_media_auto_poster.platforms.linkedin.check import run_check
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient
from social_media_auto_poster.platforms.linkedin.credentials import (
    CredentialsError,
    TokenState,
    load_linkedin_credentials,
)
from social_media_auto_poster.platforms.linkedin.publisher import LinkedInPublisher

logger = logging.getLogger(LOGGER_NAME)  # Same name as before the move, so log lines are unchanged.


@dataclass(frozen=True)
class LinkedInStatus:
    token_set: bool
    state: str  # missing, expired, expiring, valid, or unknown (no creation date given).
    expires_on: date | None
    days_left: int | None
    member: tuple[str, str] | None  # (name, author URN); only after a check that LinkedIn accepted.
    problems: tuple[str, ...]  # Plain texts; never contains the token.
    unreachable: bool  # check=True and LinkedIn could not be reached.


class RealPublishingUnavailable(Exception):
    """Real publishing cannot start; the message says why."""


def _linkedin_client(access_token: str) -> LinkedInClient:
    return LinkedInClient(access_token)


def _real_publisher(settings: Settings) -> Publisher:
    """The LinkedIn publisher, once the token in backend/.env is present, not expired, and accepted.

    The token is checked with LinkedIn (``/v2/userinfo``) here, before anything changes.
    """
    today = clock.today()
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=today)
    except CredentialsError as error:
        raise RealPublishingUnavailable(str(error)) from None
    state = credentials.state(today)
    if state is TokenState.EXPIRED:
        raise RealPublishingUnavailable(credentials.describe(today))
    if state in (TokenState.EXPIRING, TokenState.UNKNOWN):
        logger.warning("LinkedIn token: %s", credentials.describe(today))
    publisher = LinkedInPublisher(_linkedin_client(credentials.access_token))
    problem = publisher.check_access()
    if problem is not None:
        publisher.close()
        raise RealPublishingUnavailable(problem)
    return publisher


def linkedin_status(settings: Settings, *, check: bool, today: date | None = None) -> LinkedInStatus:
    """The LinkedIn token's state from backend/.env. With ``check``, also asks LinkedIn (read-only, no post)."""
    today = today or clock.today()
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=today)
    except CredentialsError as error:
        return LinkedInStatus(
            token_set=False, state="missing", expires_on=None, days_left=None, member=None,
            problems=(str(error),), unreachable=False,
        )
    state = credentials.state(today)
    expires_on = credentials.expires_on
    days_left = (expires_on - today).days if expires_on else None
    if not check or state is TokenState.EXPIRED:
        problems = (credentials.describe(today),) if state is TokenState.EXPIRED else ()
        return LinkedInStatus(
            token_set=True, state=state.value, expires_on=expires_on, days_left=days_left, member=None,
            problems=problems, unreachable=False,
        )
    with closing(_linkedin_client(credentials.access_token)) as client:
        report = run_check(credentials, client, today=today, upload_test=False)
    return LinkedInStatus(
        token_set=True, state=state.value, expires_on=expires_on, days_left=days_left, member=report.member,
        problems=tuple(report.problems), unreachable=report.unreachable,
    )
