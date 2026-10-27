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
    """Wrapper bridging json.dumps(..., separators=_SEPARATORS) to ujson.dumps.

    `json.dumps` accepts a `separators` kwarg to control item/key separators;
    with `_SEPARATORS = (",", ":")` it produces compact output with no
    whitespace after commas/colons. `ujson.dumps` does not accept a
    `separators` kwarg at all (passing it raises a TypeError), but its default
    output format is already compact -- it emits ',' and ':' with no extra
    whitespace, matching the deterministic audit log format we need. This
    wrapper simply drops the unsupported `separators` argument and calls
    ujson.dumps with sort_keys, relying on ujson's built-in compact defaults
    to preserve byte-reproducible output.
    """
    return ujson.dumps(record, sort_keys=sort_keys)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
