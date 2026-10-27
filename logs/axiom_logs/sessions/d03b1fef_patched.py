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


def parse_iso_timestamp_via_ciso8601(ts: str) -> datetime:
    """Parse a raw ISO-8601 timestamp string using ciso8601.

    ``datetime.fromisoformat`` (on Python < 3.11) cannot parse a trailing
    'Z' zulu suffix, which is why the old code routed the string through
    ``_normalize()`` first. ``ciso8601.parse_datetime`` natively accepts
    the trailing 'Z', so the raw (non-normalized) string can be passed
    straight through.

    Like ``datetime.fromisoformat``, ``ciso8601.parse_datetime`` returns a
    naive ``datetime`` when the input string has no UTC offset/timezone
    information, and a timezone-aware ``datetime`` otherwise. No further
    transformation of the returned object is needed here: this wrapper
    just swaps the parsing backend so that the UTC-assumption logic in
    ``parse()`` (which checks ``dt.tzinfo is None``) continues to work
    unchanged.
    """
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = parse_iso_timestamp_via_ciso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
