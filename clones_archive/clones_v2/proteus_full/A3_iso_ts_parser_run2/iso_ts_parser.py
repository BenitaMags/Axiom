"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone

import ciso8601


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def _parse_iso_datetime(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware (or naive) datetime.

    datetime.fromisoformat(_normalize(ts)) returns a datetime parsed from a
    stdlib-compatible ISO-8601 string, after manually rewriting a trailing
    'Z' suffix into '+00:00' since older Python versions reject 'Z' directly.

    ciso8601.parse_datetime(ts) returns an equivalent datetime object parsed
    directly from the raw ISO-8601 string, natively supporting the trailing
    'Z' zulu suffix without any pre-processing.

    This wrapper calls ciso8601.parse_datetime(ts) directly on the raw
    string, skipping the _normalize() step entirely, since ciso8601 already
    handles the 'Z' suffix natively -- making the manual normalization
    unnecessary.
    """
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _parse_iso_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
