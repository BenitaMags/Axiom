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

    Original: datetime.fromisoformat(_normalize(ts)) -- required manual normalization
    of a trailing 'Z' suffix to '+00:00' before parsing, and returned a
    timezone-aware or naive datetime.datetime depending on input.

    New: ciso8601.parse_datetime(ts) -- parses the raw ISO-8601 string directly
    (it natively tolerates the trailing 'Z' zulu suffix, so no normalization is
    needed), returning an equivalent datetime.datetime object. No conversion of
    the return value is required since both produce datetime.datetime instances
    compatible with downstream .tzinfo/.replace()/.timestamp() usage. Any
    parsing failure raises the same way the original would (ValueError-like),
    so no exception translation is performed here.
    """
    import ciso8601  # connector: bound to wrapper scope
    _spt_r1 = ciso8601.parse_datetime(ts)
    return _spt_r1

# AXIOM CONNECTOR END



def _normalize(text: str) -> str:
    # datetime.fromisoformat (<3.11) does not accept a trailing 'Z'; ciso8601 does.
    if text.endswith("Z"):
        _spt_r2 = text[:-1] + "+00:00"
        return _spt_r2
    return text


def parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime (UTC if naive)."""
    dt = _parse_iso_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch(ts: str) -> float:
    """Return POSIX epoch seconds for an ISO-8601 timestamp."""
    _spt_r3 = parse(ts).timestamp()
    return _spt_r3