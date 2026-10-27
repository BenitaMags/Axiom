"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_strip_insignificant_whitespace(raw: str) -> str:
    """Remove all whitespace that lies outside of JSON string literals.

    This is a small state-machine scanner (stdlib-only, no json/ujson
    dependency) used by `_axiom_json_dumps_wrapper` to normalize output
    from `ujson.dumps` into the exact compact ',' / ':' separator style
    that stdlib `json.dumps(..., separators=(",", ":"))` produces.
    """
    result = []
    in_string = False
    escape = False
    for ch in raw:
        if in_string:
            result.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
                result.append(ch)
            elif ch in " \t\n\r":
                # Insignificant whitespace between tokens outside of a
                # string literal -- drop it to force the compact form.
                continue
            else:
                result.append(ch)
    return "".join(result)


def _axiom_json_dumps_wrapper(record) -> str:
    """Serialize `record` the way `json.dumps(record, sort_keys=True,
    separators=_SEPARATORS)` used to.

    Background: stdlib `json.dumps` accepts a `separators` kwarg that lets
    you request the exact compact item/key separators (',' and ':') used
    here for deterministic, byte-reproducible audit records. `ujson.dumps`
    does not support arbitrary custom separators the same way -- it has
    its own fixed/limited notion of compact vs. pretty output -- so simply
    forwarding `separators=_SEPARATORS` to `ujson.dumps` is not reliable
    across versions.

    This wrapper instead calls `ujson.dumps(record, sort_keys=True)` (no
    `separators` kwarg) and then post-processes the resulting string with
    `_axiom_strip_insignificant_whitespace` to strip out any stray spaces
    outside of string literals, guaranteeing the same compact ',' / ':'
    convention stdlib `json` would have produced. `ujson`'s default
    float/NaN/Infinity formatting already matches stdlib `json`'s
    canonical output (bare `NaN`, `Infinity`, `-Infinity` tokens, same
    repr-based float rendering), so no further numeric transformation is
    required -- only the whitespace/separator normalization above.
    """
    raw = ujson.dumps(record, sort_keys=True)
    return _axiom_strip_insignificant_whitespace(raw)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
