"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone

import ciso8601


def _axiom_fromisoformat_wrapper(ts: str) -> datetime:
    """Parse a raw ISO-8601 timestamp string, bridging ciso8601 in place of
    ``datetime.fromisoformat(_normalize(ts))``.

    ``datetime.fromisoformat`` (on Python < 3.11) returns a ``datetime.datetime``
    that is timezone-naive unless an explicit UTC offset is present in the
    string, and it cannot parse a trailing 'Z' zulu suffix directly (hence the
    ``_normalize`` helper that rewrote 'Z' to '+00:00' before calling it).

    ``ciso8601.parse_datetime(ts)`` also returns a ``datetime.datetime``
    object, and it natively understands the trailing 'Z' suffix as well as
    other ISO-8601 offset forms, so no pre-normalization of the input string
    is required. This wrapper simply forwards the raw timestamp string to
    ``ciso8601.parse_datetime`` and returns its result unchanged, so that the
    caller's existing tzinfo-check/replace logic (treating a naive result as
    UTC) continues to work exactly as before.
    """
    return ciso8601.parse_datetime(ts)


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_fromisoformat_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
