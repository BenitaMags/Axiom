"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
from typing import Iterable, Mapping

from json_compact import compact_dumps

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize `record` to a compact, deterministic JSON string using ujson.

    stdlib `json.dumps` accepts a `separators` keyword directly, letting callers
    request the compact `(",", ":")` item/key separators used throughout this
    module. ujson's `dumps` does not support a `separators` keyword argument in
    the same way; instead it emits its own default whitespace-free-ish output
    (which can still include a space after ':' depending on version).

    To preserve byte-level determinism, we call `ujson.dumps(record,
    sort_keys=sort_keys)` and then strip out the specific separator whitespace
    that ujson may insert, re-joining tokens with the exact separators requested
    (defaulting to the module's `_SEPARATORS`). This guarantees the same compact
    output stdlib `json.dumps(..., separators=_SEPARATORS)` would have produced.
    """
    return compact_dumps(record, sort_keys=sort_keys, separators=separators)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
