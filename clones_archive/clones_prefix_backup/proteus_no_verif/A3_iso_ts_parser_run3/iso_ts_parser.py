"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from ciso8601 import datetime, timezone



def _ciso8601_fromisoformat_wrapper(text: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware (or naive) datetime.

    This mimics the call interface of `datetime.fromisoformat`, which returns
    a `datetime` object and (on versions < 3.11) rejects a trailing 'Z' zulu
    suffix, requiring the `_normalize` helper to rewrite it to '+00:00'.

    `ciso8601.parse_datetime`, used here as the actual parsing backend,
    returns the same kind of `datetime` object but natively accepts the
    trailing 'Z' suffix (and other ISO-8601 variants), so no normalization
    is required before calling it. We still accept pre-normalized text
    (i.e. text that has already had 'Z' replaced with '+00:00') as a
    harmless passthrough, since `ciso8601.parse_datetime` handles both
    forms identically. This lets the rest of `parse()` (the tzinfo
    check/replace logic) continue to operate unchanged, regardless of
    which parsing backend is in use.
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
