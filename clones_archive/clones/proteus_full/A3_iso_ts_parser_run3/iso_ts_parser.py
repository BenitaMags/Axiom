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


def _axiom_parse_iso8601_wrapper(text: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime, bridging ciso8601's API
    to match stdlib's datetime.fromisoformat contract.

    - stdlib datetime.fromisoformat returns a tz-naive datetime when the input
      has no UTC offset, and a tz-aware datetime otherwise; it raises ValueError
      on malformed input.
    - ciso8601.parse_datetime returns the same shape of result (tz-naive if no
      offset present, tz-aware otherwise), but raises its own parse error types
      (e.g. ValueError subclasses or, in some versions, a generic exception)
      rather than guaranteeing a plain ValueError.

    This wrapper calls ciso8601.parse_datetime and normalizes any parsing
    failure into a ValueError so callers (like parse() below, which relies on
    the naive->UTC replace() logic) see the same error contract as before.
    The returned datetime is otherwise unchanged (still naive if no offset was
    given), preserving downstream naive->UTC handling.
    """
    try:
        return ciso8601.parse_datetime(text)
    except Exception as exc:
        raise ValueError(f"Invalid ISO-8601 timestamp: {text!r}") from exc


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_parse_iso8601_wrapper(_normalize(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
