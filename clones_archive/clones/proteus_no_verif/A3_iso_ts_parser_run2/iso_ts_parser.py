"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def _ciso8601_parse_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime, bridging ciso8601's API.

    ``datetime.fromisoformat`` (pre-3.11) returns a datetime, but it cannot
    parse a trailing 'Z' zulu suffix, which is why ``_normalize`` used to
    rewrite it to '+00:00' before parsing. ``ciso8601.parse_datetime``
    returns an equivalent datetime object (naive if no UTC offset is
    present in the string, timezone-aware otherwise) and natively accepts
    the 'Z' suffix, so no pre-normalization is needed here. This wrapper
    simply calls ``ciso8601.parse_datetime`` directly on the raw timestamp
    string and returns the resulting datetime unchanged, so that the
    downstream "tzinfo is None means UTC" assumption in ``parse`` continues
    to work exactly as it did with ``datetime.fromisoformat``.
    """
    import ciso8601  # connector: bound to wrapper scope
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _ciso8601_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
