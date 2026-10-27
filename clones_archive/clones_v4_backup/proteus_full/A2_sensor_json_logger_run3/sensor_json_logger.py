"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import json
import re
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record):
    """Serialize `record` to canonical compact JSON, bridging ujson's API gaps.

    stdlib `json.dumps(record, sort_keys=True, separators=(",", ":"))` produces
    a byte-exact compact JSON string: no whitespace after ',' or ':', and
    canonical number formatting (matching Python's `repr(float)` / `repr(int)`,
    and the literal tokens "NaN", "Infinity", "-Infinity" for non-finite
    floats).

    ujson's `dumps(record, sort_keys=True)` does not accept a `separators`
    argument the way stdlib json does, so it always inserts ", " and ": "
    after each separator instead of the compact form. Its float formatting
    (precision) and NaN/Infinity spelling can also drift from stdlib json's
    output. Either discrepancy would silently break the byte-for-byte
    determinism this audit log format requires.

    This wrapper calls `dumps(record, sort_keys=True)` and then, operating
    only *outside* of quoted string content (so user data is never touched):
      1. Strips whitespace immediately following a top-level ',' or ':' to
         reproduce the compact `(",", ":")` separator format.
      2. Re-renders every bare numeric token through Python's own int/float
         formatting, and normalizes NaN/Infinity spellings, so numeric output
         matches what stdlib `json.dumps` would have produced.
    """
    raw = json.dumps(record, sort_keys=True)

    token_re = re.compile(
        r'"(?:\\.|[^"\\])*"'
        r'|(?P<sep>[,:])\s+'
        r'|(?P<num>-?(?:NaN|Infinity)|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    )

    def _replace(match):
        if match.group("sep") is not None:
            return match.group("sep")
        num = match.group("num")
        if num is not None:
            if num in ("NaN", "-NaN"):
                return "NaN"
            if num in ("Infinity", "-Infinity"):
                return num
            try:
                if any(c in num for c in ".eE"):
                    return repr(float(num))
                return repr(int(num))
            except ValueError:
                return num
        return match.group(0)

    return token_re.sub(_replace, raw)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
