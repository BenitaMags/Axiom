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


def _axiom_parse_iso8601_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime.

    ciso8601.parse_datetime(ts) natively handles the trailing 'Z' zulu suffix
    and various ISO-8601 variants, so the _normalize() workaround (previously
    needed for datetime.fromisoformat on Python <3.11) is not applied here --
    ts is passed to ciso8601 directly.

    Like datetime.fromisoformat, ciso8601.parse_datetime returns a naive
    datetime (tzinfo=None) when the input string has no timezone offset. This
    wrapper does not itself assume UTC for naive results; that UTC-assumption
    logic is applied by the caller (parse()), unchanged from before, since the
    behavior on naive timestamps is identical between the two backends.
    """
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_parse_iso8601_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
