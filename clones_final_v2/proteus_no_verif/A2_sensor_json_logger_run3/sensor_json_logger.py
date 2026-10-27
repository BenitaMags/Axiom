"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize `record` to a compact, deterministic JSON string.

    stdlib `json.dumps(record, sort_keys=True, separators=(',', ':'))`
    returns a string with no space after ',' or ':' separators, and with
    keys sorted lexicographically.

    `ujson.dumps` does not accept a `separators` keyword argument at all,
    and when given `sort_keys=True` it still inserts a space after ':'
    (and sometimes after ',') by default -- so its raw output is not
    byte-identical to what stdlib json produces with compact separators.

    This wrapper calls `ujson.dumps(record, sort_keys=True)` and then
    post-processes the resulting string to strip any whitespace that
    ujson inserted immediately after ',' or ':' characters that fall
    outside of string literals, restoring byte-identical compact output
    to the required (',', ':') separator style. This is done with a
    small state machine that tracks whether we are inside a JSON string
    (respecting backslash escapes) so we never touch whitespace that is
    part of an actual string value.
    """
    raw = ujson.dumps(record, sort_keys=sort_keys)

    out = []
    in_string = False
    escape = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        out.append(ch)
        if not in_string:
            if ch == '"':
                in_string = True
            elif ch in (",", ":"):
                # Skip any whitespace ujson inserted after separators.
                j = i + 1
                while j < n and raw[j] in (" ", "\t", "\n", "\r"):
                    j = j + 1
                i = j
                continue
        else:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        i = i + 1
    _spt_r1 = "".join(out)

    return _spt_r1


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    _spt_r2 = _axiom_json_dumps_wrapper({"tag": tag, "value": value, "ts": ts, "quality": quality}, sort_keys=True, separators=_SEPARATORS)
    return _spt_r2


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    _spt_r3 = "\n".join(format_reading(**dict(r)) for r in readings)
    return _spt_r3
