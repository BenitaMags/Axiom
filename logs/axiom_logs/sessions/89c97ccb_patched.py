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
import ciso8601


def parse_datetime_ciso8601(ts: str):
    """Wrapper around ciso8601.parse_datetime replacing datetime.fromisoformat(_normalize(ts)).

    Original: datetime.fromisoformat(_normalize(ts)) -> parses an ISO-8601 string
    (after normalizing a trailing 'Z' to '+00:00') into a datetime, raising
    ValueError on malformed input.

    New: ciso8601.parse_datetime(ts) -> parses the raw ISO-8601 string directly
    (natively tolerant of a trailing 'Z', so no normalization is needed), raising
    its own parsing exception on malformed input.

    This wrapper calls ciso8601.parse_datetime(ts) directly on the unmodified
    timestamp, and translates any parse failure into a ValueError so callers
    relying on the original error contract are unaffected. The returned
    datetime is passed through unchanged, so the caller's tzinfo-is-None
    UTC-assignment logic still applies exactly as before.
    """
    try:
        return ciso8601.parse_datetime(ts)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc




def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = parse_datetime_ciso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()