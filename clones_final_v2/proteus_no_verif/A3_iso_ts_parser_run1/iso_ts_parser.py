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


def _parse_iso8601(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime object.

    Original: datetime.fromisoformat(_normalize(ts)) -- required stripping a
    trailing 'Z' zulu suffix via _normalize() since stdlib fromisoformat
    (pre-3.11) cannot parse it directly, then parsed with datetime.fromisoformat.

    New: ciso8601.parse_datetime(ts) -- parses the raw timestamp string directly,
    natively tolerating the trailing 'Z' suffix, so no normalization step is
    needed. Returns a datetime equivalent to the original (naive if the input
    had no offset, tz-aware otherwise); the caller's existing UTC fallback for
    naive results is left untouched.
    """
    _spt_r1 = ciso8601.parse_datetime(ts)
    return _spt_r1




def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        _spt_r2 = text[:-1] + "+00:00"
        return _spt_r2
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _parse_iso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    _spt_r3 = parse(ts).timestamp()
    return _spt_r3