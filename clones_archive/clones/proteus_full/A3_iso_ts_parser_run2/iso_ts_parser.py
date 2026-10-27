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


def _ciso8601_parse_datetime_as_fromisoformat(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string, mimicking datetime.fromisoformat(_normalize(ts)).

    ciso8601.parse_datetime(ts) natively accepts a trailing 'Z' zulu suffix
    (returning an aware datetime with UTC offset), whereas
    datetime.fromisoformat on Python < 3.11 does not accept 'Z' at all --
    which is why the previous code normalized 'Z' to '+00:00' before calling
    fromisoformat. Since ciso8601 handles 'Z' directly, we call it on the raw,
    un-normalized timestamp string. The resulting datetime has the same
    naive/aware semantics as before: aware (with UTC tzinfo) if the input had
    an explicit offset or 'Z', naive if the input had no offset at all. This
    preserves the contract expected by parse(), which defaults naive results
    to UTC.
    """
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _ciso8601_parse_datetime_as_fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
