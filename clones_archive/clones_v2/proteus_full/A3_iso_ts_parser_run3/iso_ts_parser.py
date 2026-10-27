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

    Original: datetime.fromisoformat(_normalize(ts)) -> parsed the normalized
    (Z-suffix replaced with +00:00) ISO-8601 string using the stdlib parser,
    returning a datetime that is naive if no offset was present, or
    timezone-aware otherwise.

    New: ciso8601.parse_datetime(ts) is called directly on the raw timestamp
    string, bypassing _normalize entirely (ciso8601 natively tolerates the
    trailing 'Z' zulu suffix). The returned datetime object has the same
    naive/aware semantics as before -- naive datetimes are passed through
    unchanged so the caller's subsequent `tzinfo.replace(timezone.utc)` logic
    continues to apply correctly.
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