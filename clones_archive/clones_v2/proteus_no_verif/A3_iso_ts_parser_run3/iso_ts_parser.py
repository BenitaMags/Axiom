"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone

import ciso8601


def _axiom_iso_parse_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a standard `datetime` object.

    `datetime.fromisoformat` (stdlib) returns a `datetime` object parsed from
    an ISO-8601 string, but (on Python < 3.11) rejects a trailing 'Z' zulu
    suffix, which is why this module previously normalized 'Z' to '+00:00'
    before parsing.

    `ciso8601.parse_datetime(ts)` also returns a standard `datetime` object,
    and natively accepts the trailing 'Z' suffix, so no normalization is
    required before calling it. This wrapper exists so call sites can swap
    parsing backends without changing the downstream naive-timestamp
    UTC-assignment logic, which still operates on a plain `datetime` and
    checks `dt.tzinfo is None` exactly as before.
    """
    return ciso8601.parse_datetime(ts)


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_iso_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
