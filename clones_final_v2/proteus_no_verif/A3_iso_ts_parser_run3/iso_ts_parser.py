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
        _spt_r1 = text[:-1] + "+00:00"
        return _spt_r1
    return text


def _axiom_ciso8601_parse_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime object using ciso8601.

    ciso8601.parse_datetime(ts) returns a datetime object directly (timezone-aware
    if the input string carries an offset/'Z', naive otherwise) -- unlike
    datetime.fromisoformat, ciso8601 natively understands a trailing 'Z' zulu
    suffix, so the _normalize() workaround that rewrites 'Z' to '+00:00' is not
    needed here and is bypassed. This wrapper simply calls ciso8601 directly and
    returns the resulting datetime object unchanged.
    """
    _spt_r2 = ciso8601.parse_datetime(ts)
    return _spt_r2


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_ciso8601_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    _spt_r3 = parse(ts).timestamp()
    return _spt_r3
