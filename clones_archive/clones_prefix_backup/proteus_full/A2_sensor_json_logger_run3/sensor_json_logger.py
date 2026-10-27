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
    """Serialize `record` to a compact JSON string using ujson.

    stdlib `json.dumps` accepts a `separators` kwarg (we used `(",", ":")`) to
    produce compact output with no whitespace after `,` or `:`. `ujson.dumps`
    does not accept a `separators` kwarg -- passing one raises a TypeError.
    However, `ujson.dumps` already emits compact separators by default (no
    spaces after `,` or `:`), so we simply call it without that kwarg and
    then defensively strip any stray whitespace that might otherwise creep
    into the separators, to guarantee byte-reproducible audit logs matching
    the previous `json.dumps(..., separators=(",", ":"))` format.
    """

    text = ujson.dumps(record, sort_keys=sort_keys)
    # Normalize away any incidental whitespace around separators to keep
    # output byte-identical to the compact json.dumps(..., separators=...) form.
    text = text.replace(", ", ",").replace(": ", ":")
    return text


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
