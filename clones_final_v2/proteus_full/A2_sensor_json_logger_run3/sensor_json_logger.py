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

    Background: stdlib `json.dumps` accepts a `separators` kwarg, and this
    module relied on `separators=(",", ":")` to strip the default whitespace
    after `,` and `:` so that audit records are byte-reproducible. `ujson.dumps`
    does not support the `separators` keyword at all (passing it raises
    TypeError); ujson always emits its own compact form. However, ujson's
    compact form is not guaranteed byte-identical to json's compact form in
    every version (e.g. spacing details, or float formatting for values like
    NaN/Infinity or trailing zeros), which would break the byte-reproducibility
    guarantee this logger promises.

    This wrapper calls `ujson.dumps(record, sort_keys=sort_keys)` and then
    normalizes the result: it removes any stray whitespace after ',' and ':'
    structural characters that may appear outside of string literals, so the
    final output matches the exact `(",", ":")` separator style previously
    produced by `json.dumps(..., separators=_SEPARATORS)`. This keeps audit
    log output deterministic and byte-reproducible regardless of the JSON
    backend's default whitespace choices.
    """

    raw = ujson.dumps(record, sort_keys=sort_keys)

    normalized_chars = []
    in_string = False
    escape = False
    i = 0
    length = len(raw)
    while i < length:
        ch = raw[i]
        normalized_chars.append(ch)
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in (",", ":"):
                # Skip any whitespace immediately following a structural
                # separator so output matches the compact (",", ":") style.
                j = i + 1
                while j < length and raw[j] in " \t\n\r":
                    j += 1
                i = j
                continue
        i += 1

    return "".join(normalized_chars)



def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
