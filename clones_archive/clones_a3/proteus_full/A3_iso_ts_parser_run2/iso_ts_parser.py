"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone
import ciso8601


def _axiom_fromisoformat_wrapper(ts_or_normalized: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware (or naive) datetime.

    This wrapper exists to bridge the API difference between
    `datetime.fromisoformat` and `ciso8601.parse_datetime`:

    - `datetime.fromisoformat` returns a `datetime.datetime` and raises a
      `ValueError` on malformed input.
    - `ciso8601.parse_datetime` also returns a `datetime.datetime`, but on
      malformed input it raises `ciso8601.ParseError` instead of
      `ValueError`.

    To preserve the original error-handling contract (callers catching
    `ValueError` around timestamp parsing), this wrapper calls
    `ciso8601.parse_datetime` and translates any `ciso8601.ParseError`
    into a `ValueError`.
    """
    try:
        return ciso8601.parse_datetime(ts_or_normalized)
    except ciso8601.ParseError as exc:
        raise ValueError(str(exc)) from exc


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_fromisoformat_wrapper(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
