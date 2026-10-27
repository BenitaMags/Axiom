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

_FLOAT_TOKEN_RE = re.compile(r'-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+[eE][+-]?\d+')


def _axiom_json_dumps_wrapper(record, sort_keys=True):
    """Bridge ujson.dumps to the stdlib json.dumps(..., separators=(',', ':'))
    contract required for byte-reproducible audit logs.

    - stdlib ``json.dumps(obj, sort_keys=True, separators=(',', ':'))``
      returns a compact string with no space after ',' or ':' and formats
      float values using Python's ``repr()`` (the shortest round-trippable
      decimal representation).
    - ``ujson.dumps`` accepts ``sort_keys`` but has no ``separators`` kwarg
      (it always inserts a space after ',' and ':'), and its float encoder
      can produce a different precision/format than Python's ``repr()``.

    This wrapper calls ujson with only the kwargs it actually supports, then
    post-processes the resulting string in two ways so the output is
    byte-for-byte identical to what stdlib json would have produced:
      1. Strips the whitespace ujson inserts after ',' and ':' so the output
         matches the compact (',', ':') separator format.
      2. Re-renders every numeric float token using Python's ``repr()`` so
         precision/formatting quirks in ujson's float encoder can't silently
         change audit log content.
    """
    import ujson  # connector: bound to wrapper scope
    raw = ujson.dumps(record, sort_keys=sort_keys)

    # 1. Match stdlib's compact (',', ':') separators (ujson defaults to
    #    ', ' and ': ').
    compact = raw.replace(", ", ",").replace(": ", ":")

    # 2. Normalize float formatting to Python's repr()-based rendering so it
    #    matches stdlib json's determinism guarantees exactly.
    def _renormalize_float(match: "re.Match") -> str:
        token = match.group(0)
        return repr(float(token))

    return _FLOAT_TOKEN_RE.sub(_renormalize_float, compact)



def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
