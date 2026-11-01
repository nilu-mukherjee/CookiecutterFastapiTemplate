from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current time, timezone-aware UTC.

    Code that must be testable against the clock calls this through the module
    (``from app.core import time as clock; clock.utcnow()``) so tests can patch it.
    """
    return datetime.now(UTC)
