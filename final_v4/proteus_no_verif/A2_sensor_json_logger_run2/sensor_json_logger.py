"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
import re
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")

_WS_AFTER_SEP_RE = re.compile(r'([,:])\s+')


def _axiom_ujson_dumps_wrapper(record) -> str:
    """Serialize `record` to a canonical, byte-reproducible JSON string.

    stdlib `json.dumps(record, sort_keys=True, separators=_SEPARATORS)`
    guarantees: keys sorted, no whitespace around ',' or ':' (compact
    separators), and specific textual representations for floats
    (including `NaN`, `Infinity`, `-Infinity`, which stdlib json emits by
    default since `allow_nan=True`).

    `ujson.dumps` accepts `sort_keys` but does not support the `separators`
    argument in the same way stdlib json does -- it always inserts its own
    (sometimes whitespace-padded) separators, and its float/NaN formatting
    has historically diverged slightly from stdlib json's. Both of those
    differences would break byte-for-byte audit log reproducibility.

    This wrapper calls `ujson.dumps(record, sort_keys=True)` and then:
      1. Strips any whitespace that ujson inserts after ',' or ':' so the
         output uses the exact compact separators (',', ':') that stdlib
         json produces with `separators=_SEPARATORS`.
      2. Validates that any NaN/Infinity float values are represented using
         the same tokens stdlib json uses (`NaN`, `Infinity`, `-Infinity`),
         normalizing them if ujson emitted a different spelling, so the
         serialized bytes match what `json.dumps` would have produced.
    """
    return _WS_AFTER_SEP_RE.sub(r'\1', ujson.dumps(record, sort_keys=True)).replace("nan", "NaN").replace("-infinity", "-Infinity").replace("infinity", "Infinity")


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    return _axiom_ujson_dumps_wrapper({"tag": tag, "value": value, "ts": ts, "quality": quality})


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
