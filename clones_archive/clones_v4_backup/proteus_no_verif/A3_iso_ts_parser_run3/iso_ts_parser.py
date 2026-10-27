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
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    `datetime.fromisoformat` (the original backend) returns a naive datetime
    when the input string has no UTC offset, and (on Python < 3.11) rejects
    strings using the trailing 'Z' zulu suffix -- hence the `_normalize` step
    that rewrites 'Z' to '+00:00' before parsing.

    `ciso8601.parse_datetime` natively accepts the trailing 'Z' suffix, so
    normalization is unnecessary for that case, but it otherwise returns the
    same shape of result as `fromisoformat`: a naive datetime when the input
    string carries no offset. This wrapper therefore keeps the tzinfo-is-None
    -> UTC replacement logic so callers keep receiving timezone-aware
    datetimes (UTC-assumed for naive RTU-style timestamps) exactly as before.
    """
    dt = ciso8601.parse_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    return _axiom_parse_iso8601_wrapper(ts)


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
