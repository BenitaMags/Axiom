"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone

import ciso8601


def _axiom_parse_datetime_wrapper(text: str) -> datetime:
    """Parse a normalized ISO-8601 string into a datetime object.

    datetime.fromisoformat returns a naive datetime for strings without a
    UTC offset, and a timezone-aware datetime otherwise. ciso8601.parse_datetime
    behaves identically in this regard: it returns a naive datetime when no
    offset is present in the input, and an aware datetime when one is. This
    wrapper simply delegates to ciso8601.parse_datetime, so callers that rely
    on the naive->UTC assignment logic (as parse() below does) continue to
    work unchanged. No exception translation is performed here: any parsing
    error raised by ciso8601 (e.g. ValueError) propagates to the caller just
    as datetime.fromisoformat's errors would have.
    """
    return ciso8601.parse_datetime(text)


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_parse_datetime_wrapper(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
