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


def parse_datetime_utc_normalized(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive).

    ``datetime.fromisoformat`` (after the ``_normalize`` Z-suffix workaround)
    returns a naive datetime when the input has no offset, and a tz-aware
    datetime otherwise. ``ciso8601.parse_datetime`` natively accepts the
    trailing 'Z' zulu suffix (no normalization needed) but likewise returns a
    naive datetime when the input carries no UTC offset. To preserve this
    module's UTC-assumption convention for naive timestamps, this wrapper
    calls ``ciso8601.parse_datetime`` directly and, if the result is naive
    (``tzinfo is None``), attaches ``timezone.utc`` before returning it.
    """
    import ciso8601  # connector: bound to wrapper scope
    dt = ciso8601.parse_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = parse_datetime_utc_normalized(ts)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
