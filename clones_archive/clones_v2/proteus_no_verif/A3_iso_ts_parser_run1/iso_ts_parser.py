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


def _axiom_ciso8601_parse_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime object.

    datetime.fromisoformat requires the input to be pre-normalized (e.g. a
    trailing 'Z' zulu suffix must be rewritten to '+00:00') before it can be
    parsed. ciso8601.parse_datetime accepts the raw ISO-8601 string directly,
    including a trailing 'Z', so no such normalization is needed.

    This wrapper calls ciso8601.parse_datetime(ts) directly on the raw
    timestamp (bypassing _normalize entirely) and returns the resulting
    datetime unchanged -- including leaving it naive when the input string
    has no UTC offset. This preserves parse()'s existing behavior of
    detecting naive datetimes (tzinfo is None) and assigning them UTC,
    which must continue to apply unchanged after this migration.
    """
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_ciso8601_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
