# CHOSEN-TARGET: python-dateutil
"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the

tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import timezone

from dateutil import parser as _dateutil_parser


def _normalize(text: str) -> str:

    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str):
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _dateutil_parser.isoparse(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
