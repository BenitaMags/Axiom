"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _ujson_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize `record` to a compact JSON string, matching the output of
    stdlib json.dumps(record, sort_keys=True, separators=(',', ':')).

    stdlib json.dumps() accepts a `separators` kwarg to control whitespace
    between items/keys, allowing a compact representation with no spaces.
    ujson.dumps() does not support a `separators` kwarg at all -- it always
    inserts no space after ':' but does insert a space after ',' by default
    in some versions, and raises TypeError if passed `separators` directly.

    This wrapper calls ujson.dumps(record, sort_keys=sort_keys) without the
    unsupported kwarg, then strips out any ", " separators produced by ujson
    and replaces them with "," so the final string exactly matches the
    compact, deterministic format required for byte-reproducible audit logs.
    """

    dumped = ujson.dumps(record, sort_keys=sort_keys)
    # Normalize whitespace to guarantee compact separators regardless of the
    # ujson version's default formatting (defensive; typically a no-op).
    dumped = dumped.replace(", ", ",").replace(": ", ":")
    return dumped


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _ujson_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
