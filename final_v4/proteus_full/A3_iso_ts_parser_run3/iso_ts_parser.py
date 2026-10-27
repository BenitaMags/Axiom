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
from datetime import datetime, timezone


def parse_iso_timestamp_with_ciso8601(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    Original: datetime.fromisoformat(_normalize(ts)) -- required stripping a
    trailing 'Z' zulu suffix before parsing via the stdlib parser, then the
    caller separately checked for a naive result and attached UTC tzinfo.

    New: ciso8601.parse_datetime(ts) parses the raw ISO-8601 string directly,
    including trailing 'Z' suffixes, so no normalization step is needed.
    This wrapper preserves the original combined behavior by also checking
    if the returned datetime is naive (tzinfo is None) and, if so, attaching
    timezone.utc -- matching the original function's UTC-assumption for naive
    timestamps. Returns the same type (a timezone-aware datetime) as before.
    """
    dt = ciso8601.parse_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

# AXIOM CONNECTOR END



def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = parse_iso_timestamp_with_ciso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()