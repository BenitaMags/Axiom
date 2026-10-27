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


def _ciso8601_parse_datetime(ts: str):
    """Wrapper around ciso8601.parse_datetime replacing datetime.fromisoformat(_normalize(ts)).

    Original: datetime.fromisoformat(_normalize(ts)) -> parses a normalized ISO-8601
    string (with trailing 'Z' converted to '+00:00') using the stdlib parser, raising
    ValueError on malformed input.

    New: ciso8601.parse_datetime(ts) -> parses the raw ISO-8601 string directly,
    natively tolerating a trailing 'Z' suffix (no normalization needed), but may
    raise parser-specific exceptions (e.g. ValueError subclasses or internal errors)
    on malformed input.

    Transformation: calls ciso8601.parse_datetime(ts) directly on the original
    timestamp string, catches any exception raised during parsing and re-raises it
    as a ValueError to preserve the original error-handling contract. Returns the
    resulting datetime object unchanged (tzinfo-naive-to-UTC handling remains the
    caller's responsibility, unchanged from before).
    """
    try:
        return ciso8601.parse_datetime(ts)
    except Exception as exc:
        raise ValueError(str(exc)) from exc




def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _ciso8601_parse_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()