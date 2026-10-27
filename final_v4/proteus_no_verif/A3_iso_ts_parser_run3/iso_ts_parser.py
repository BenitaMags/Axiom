"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from zoneinfo import ZoneInfo

# Stand-in for UTC that does not depend on the `datetime` import above (which
# is slated for removal once nothing in this module references it anymore).
_UTC = ZoneInfo("UTC")


def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        _spt_r1 = text[:-1] + "+00:00"
        return _spt_r1
    return text


def _ciso8601_parse_wrapper(ts: str):
    """Parse an ISO-8601 timestamp string with ciso8601.

    ``datetime.fromisoformat`` (the function this wrapper replaces) required
    the ``_normalize`` workaround above because, on Python versions before
    3.11, it rejects a trailing 'Z' zulu suffix -- ``_normalize`` rewrote
    'Z' to '+00:00' before handing the string to ``fromisoformat``.

    ``ciso8601.parse_datetime`` accepts the trailing 'Z' natively, so this
    wrapper calls it directly on the raw, un-normalized string and returns
    the resulting ``datetime.datetime`` object unchanged. The returned value
    has the same shape as what ``fromisoformat`` produced (a ``datetime``,
    timezone-aware if the string carried an offset, naive otherwise), so
    downstream tz/epoch handling in ``parse()`` works unmodified.
    """
    import ciso8601  # connector: bound to wrapper scope
    _spt_r2 = ciso8601.parse_datetime(ts)
    return _spt_r2


def parse(ts: str):
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _ciso8601_parse_wrapper(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    _spt_r3 = parse(ts).timestamp()
    return _spt_r3
