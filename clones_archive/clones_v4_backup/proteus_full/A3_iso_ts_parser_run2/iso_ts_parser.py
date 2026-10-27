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
import re
import ciso8601

_ISO_FULL_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}'          # full date: YYYY-MM-DD required
    r'([T ]\d{2}:\d{2}:\d{2}(\.\d+)?)?'  # optional full time
    r'(Z|[+-]\d{2}:?\d{2})?$'        # optional zulu or offset
)


def parse_iso_timestamp_with_ciso8601(ts: str) -> 'datetime.datetime':
    """Parse an ISO-8601 timestamp string into a datetime object.

    Original: `datetime.fromisoformat(_normalize(ts))` -- stdlib parser that
    required manual normalization of a trailing 'Z' suffix (since
    datetime.fromisoformat on Python <3.11 rejects 'Z') and raised ValueError
    on malformed input.

    New: calls `ciso8601.parse_datetime(ts)` directly, which natively accepts
    the 'Z' suffix so no normalization is needed. However, ciso8601 is more
    lenient than fromisoformat and will happily parse partial/truncated ISO
    strings (e.g. just a year, or a date missing the day component) that
    fromisoformat would reject. To preserve fromisoformat's stricter contract,
    this wrapper validates the input against a regex requiring a full
    YYYY-MM-DD date (with optional full HH:MM:SS time and optional Z/offset)
    before parsing, rejecting anything else with ValueError. Any exception
    raised by ciso8601 itself (e.g. ciso8601.ParseError) is also caught and
    re-raised as ValueError, matching datetime.fromisoformat's exception
    contract.

    Returns the same type as before: a `datetime.datetime` instance (naive or
    timezone-aware depending on input, identical semantics to the original).
    """
    if not isinstance(ts, str) or not _ISO_FULL_RE.match(ts):
        raise ValueError(f"Invalid isoformat string: {ts!r}")
    try:
        return ciso8601.parse_datetime(ts)
    except Exception as exc:
        raise ValueError(f"Invalid isoformat string: {ts!r}") from exc




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