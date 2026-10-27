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


def _axiom_fromisoformat_wrapper(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware (or naive)
    datetime object, bridging the API difference between the stdlib
    ``datetime.fromisoformat`` and ``ciso8601.parse_datetime``.

    ``datetime.fromisoformat(_normalize(ts))`` returns a ``datetime`` built
    from the (possibly Z-normalized) string, but on Python versions before
    3.11 it cannot parse a trailing 'Z' zulu suffix, hence the pre-existing
    ``_normalize`` step.

    ``ciso8601.parse_datetime(ts)`` returns a ``datetime`` object as well,
    but it natively understands the trailing 'Z' suffix (and other ISO-8601
    variants), so no normalization is required before calling it.

    This wrapper simply delegates to ``ciso8601.parse_datetime`` and returns
    the resulting ``datetime`` unchanged, since downstream code only relies
    on standard ``datetime`` attributes/methods (``tzinfo``, ``replace``,
    ``timestamp``), which are fully compatible between the two return
    values.
    """
    return ciso8601.parse_datetime(ts)


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _axiom_fromisoformat_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()
