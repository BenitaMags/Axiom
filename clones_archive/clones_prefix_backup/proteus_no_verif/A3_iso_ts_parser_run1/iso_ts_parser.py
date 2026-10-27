"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from ciso8601 import datetime, timezone
import ciso8601


def _iso8601_parse_wrapper(ts: str) -> datetime:
    """Drop-in replacement for datetime.fromisoformat(_normalize(ts)).

    datetime.fromisoformat (<3.11) returns a naive or aware datetime parsed
    from a strict ISO-8601 string, but does not accept a trailing 'Z' zulu
    suffix, which is why callers previously normalized the string first via
    `_normalize`. ciso8601.parse_datetime(ts) returns an equivalent datetime
    object directly from the raw, un-normalized string, natively accepting
    the trailing 'Z' suffix (and other ISO-8601 variants). Since ciso8601
    already produces the same kind of datetime object (naive if no offset
    present, aware otherwise), no further transformation of the return value
    is required -- we simply skip the now-unnecessary `_normalize` step and
    call ciso8601 directly with the original timestamp string.
    """
    return ciso8601.parse_datetime(ts)


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _iso8601_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
