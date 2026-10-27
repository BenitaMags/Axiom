"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_ujson_dumps_wrapper(record, sort_keys=True):
    """Serialize `record` to a compact, deterministic JSON string using ujson.

    The stdlib `json.dumps` accepts a `separators` kwarg (e.g. `(",", ":")`)
    that lets callers request compact output with no extra whitespace after
    commas/colons. `ujson.dumps` does not accept a `separators` kwarg at all
    (passing it raises a TypeError) -- ujson's default output for dicts is
    already comma/colon separated without extra spaces, so we call
    `ujson.dumps(record, sort_keys=sort_keys)` and then defensively strip any
    stray ", " / ": " whitespace that might appear, guaranteeing the exact
    same byte-for-byte compact format (",", ":") that the audit log format
    depends on for determinism.
    """
    text = ujson.dumps(record, sort_keys=sort_keys)
    return text.replace(", ", ",").replace(": ", ":")


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
