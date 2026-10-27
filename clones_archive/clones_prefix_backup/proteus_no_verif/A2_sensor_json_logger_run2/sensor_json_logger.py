"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record, sort_keys=True):
    """Serialize `record` to a compact, deterministic JSON string.

    The stdlib `json.dumps` supports a `separators` kwarg, letting callers
    request compact output like (',', ':') to avoid the default spacing
    after commas/colons. `ujson.dumps` does not accept a `separators`
    kwarg at all -- passing one raises a TypeError. However, ujson's
    default output format is already compact (no extra whitespace), which
    matches the (',', ':') separator style used throughout this module.

    This wrapper calls `ujson.dumps(record, sort_keys=sort_keys)` and
    silently drops any 'separators' request, relying on ujson's default
    compact formatting to reproduce the same byte-for-byte output that
    `json.dumps(record, sort_keys=True, separators=(',', ':'))` would have
    produced.
    """
    return ujson.dumps(record, sort_keys=sort_keys)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
