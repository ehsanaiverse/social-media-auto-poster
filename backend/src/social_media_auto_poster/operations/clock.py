"""The current time, in one place, so tests can replace it.

Call these through the module (``clock.now()``), never ``from ...clock import now``: a test that replaces
``clock.now`` then reaches every caller, and no test depends on the real clock.
"""

from datetime import UTC, date, datetime


def now() -> datetime:
    """The current time for scheduling."""
    return datetime.now(UTC)


def utc_now() -> datetime:
    """The current time for notes written on jobs (resolve)."""
    return datetime.now(UTC)


def today() -> date:
    """Today's local date, for the LinkedIn token's expiry."""
    return date.today()
