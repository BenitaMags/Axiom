"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from ciso8601 import datetime, timezone
import ciso8601


def _ciso8601_fromisoformat_wrapper(text: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware (or naive) datetime.

    ``datetime.fromisoformat`` parses an ISO-8601 string and returns a
    ``datetime`` object, using the stdlib's own parsing rules.

    ``ciso8601.parse_datetime`` parses the same kind of ISO-8601 string but
    returns a ``datetime`` object built by its own (faster) C-based parser;
    the returned object is a regular ``datetime`` instance, compatible with
    downstream ``tzinfo``/``replace``/``timestamp()`` usage.

    This wrapper simply forwards the (already-normalized) text to
    ``ciso8601.parse_datetime`` and returns its result unchanged, so callers
    that previously relied on ``datetime.fromisoformat`` keep working with
    ciso8601 as the backend.
    """
    return ciso8601.parse_datetime(text)


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _ciso8601_fromisoformat_wrapper(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
