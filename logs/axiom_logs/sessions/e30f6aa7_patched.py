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


def _parse_iso_ts_with_ciso8601(ts: str):
    """Wrapper replacing `datetime.fromisoformat(_normalize(ts))`.

    Original: stdlib `datetime.fromisoformat` applied to a Z-normalized string,
    returning a `datetime` (naive or aware) and raising `ValueError` on malformed
    input.

    New: calls `ciso8601.parse_datetime(ts)` directly on the raw (un-normalized)
    timestamp string -- ciso8601 natively accepts the trailing 'Z' zulu suffix,
    so the `_normalize` Z-stripping step is skipped entirely. Returns a
    `datetime` object, matching the original return type/semantics.

    Any parse failure raised by ciso8601 (which may not subclass ValueError,
    e.g. `ValueError` variants or internal parse errors) is normalized/re-raised
    as `ValueError` to preserve the original error-handling contract.
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
    dt = _parse_iso_ts_with_ciso8601(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    return parse(ts).timestamp()