"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone

import ciso8601


def _axiom_ciso8601_parse_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime object.

    datetime.fromisoformat (<3.11) returns a datetime parsed from a string
    that must not have a trailing 'Z' zulu suffix (it must be pre-normalized,
    e.g. 'Z' replaced with '+00:00', via the local `_normalize` helper).

    ciso8601.parse_datetime(ts) returns a datetime object as well, but it
    natively understands the trailing 'Z' suffix (and other ISO-8601
    variants), so no pre-normalization is required before calling it.

    This wrapper simply calls ciso8601.parse_datetime(ts) directly on the
    raw, un-normalized timestamp string and returns the resulting datetime
    unchanged, so that the existing tzinfo-check / replace / timestamp()
    chain in `parse()` continues to work exactly as before.
    """
    return ciso8601.parse_datetime(ts)


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_ciso8601_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
