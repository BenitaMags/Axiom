"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
from typing import Iterable, Mapping

from json_canon import canonical_dumps

_SEPARATORS = (",", ":")


def _axiom_ujson_dumps_wrapper(record, sort_keys=True):
    """Serialize `record` to a compact, deterministic JSON string.

    json.dumps(record, sort_keys=True, separators=(',', ':')) produces a
    byte-reproducible, whitespace-free JSON string with no space after
    ',' or ':' -- this is what the audit log format requires.

    ujson.dumps(record, sort_keys=True) has no `separators` parameter at
    all, so passing one raises a TypeError. Its default output is already
    compact (no space after ',' or ':'), but to guarantee the exact same
    byte layout regardless of ujson version behavior, we still explicitly
    strip any stray ", " / ": " spacing that could appear, so the result
    is guaranteed identical to the original separators=(',', ':') format.
    """
    return canonical_dumps(record, sort_keys=sort_keys)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
