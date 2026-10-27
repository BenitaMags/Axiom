"""iso_ts_parser -- ISO-8601 timestamp parsing for event/alarm logs.

Parses ISO-8601 timestamps into timezone-aware datetimes and epoch seconds. Naive
timestamps are assumed UTC (a common RTU convention). The parser backend is the
substitution target: `datetime.fromisoformat` (stdlib) -> `ciso8601` (faster, and
tolerant of the trailing 'Z' zulu suffix).
"""
from datetime import datetime, timezone


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: datetime → ciso8601
# The following functions bridge API incompatibilities between
# datetime and ciso8601. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _parse_iso_datetime(ts: str) -> datetime:
    """Wrapper around ciso8601.parse_datetime replacing datetime.fromisoformat(_normalize(ts)).

    Original: datetime.fromisoformat(_normalize(ts)) -- required manual stripping
    of a trailing 'Z' zulu suffix (via _normalize) before parsing, since
    datetime.fromisoformat (<3.11) cannot handle 'Z' directly. Returned a
    datetime (naive if no offset present).

    New: ciso8601.parse_datetime(ts) -- parses the raw ISO-8601 string directly,
    natively tolerating the trailing 'Z' suffix, so no normalization is needed.
    Returns a datetime with the same tzinfo-is-None semantics for naive inputs,
    leaving UTC-assignment for naive timestamps to the caller (parse()).
    """
    import ciso8601  # connector: bound to wrapper scope
    return ciso8601.parse_datetime(ts)



def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _parse_iso_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()