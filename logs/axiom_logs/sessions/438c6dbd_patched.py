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
def _parse_datetime_ciso8601(ts: str) -> datetime:
    """Wrapper around ciso8601.parse_datetime replacing datetime.fromisoformat(_normalize(ts)).

    Original: datetime.fromisoformat(_normalize(ts)) -- stdlib ISO-8601 parser
    requiring the trailing 'Z' zulu suffix to be normalized to '+00:00' before
    parsing, returning a naive or aware datetime depending on input.

    New: ciso8601.parse_datetime(ts) -- parses the raw timestamp directly
    (including native support for trailing 'Z'), returning a naive or aware
    datetime with the same semantics expected downstream by parse(). No
    normalization is needed and none is performed.
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
    dt = _parse_datetime_ciso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()