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


def _parse_iso8601_with_ciso8601(ts: str) -> "datetime":
    """Parse an ISO-8601 timestamp string using ciso8601 instead of datetime.fromisoformat.

    Original behavior: `datetime.fromisoformat(_normalize(ts))` first normalized a
    trailing 'Z' suffix to '+00:00' (since stdlib fromisoformat pre-3.11 rejects 'Z'),
    then parsed via datetime.fromisoformat, yielding a naive datetime for inputs with
    no offset and a timezone-aware (UTC-offset) datetime for inputs with an explicit
    offset or 'Z'.

    New behavior: calls ciso8601.parse_datetime(ts) directly on the raw, un-normalized
    string (ciso8601 natively accepts trailing 'Z' and arbitrary offsets, so no
    pre-processing or fallback to fromisoformat is needed). ciso8601.parse_datetime
    already matches fromisoformat's contract: it returns a naive datetime (tzinfo=None)
    when the input has no offset/'Z', and a timezone-aware datetime (tzinfo set to the
    appropriate UTC offset) when the input includes an offset or 'Z' suffix.

    This wrapper therefore requires no additional conversion -- it simply swaps the
    parsing backend while preserving the exact naive-vs-aware return contract that
    downstream code (the `if dt.tzinfo is None` UTC-assumption check in `parse`)
    depends on.

    Args:
        ts: ISO-8601 timestamp string, possibly with a trailing 'Z' or explicit offset.

    Returns:
        datetime: naive if `ts` had no offset/'Z', else timezone-aware (UTC-offset).
    """
    return ciso8601.parse_datetime(ts)




def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _parse_iso8601_with_ciso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()