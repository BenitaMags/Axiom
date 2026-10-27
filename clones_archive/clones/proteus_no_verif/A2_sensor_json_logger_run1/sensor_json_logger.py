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
    """Serialize ``record`` to a compact, deterministic JSON string.

    stdlib ``json.dumps`` accepts a ``separators`` keyword argument (here
    ``(",", ":")``) that suppresses the default whitespace after item and
    key separators, giving compact, byte-for-byte reproducible output.
    ``ujson.dumps`` has no ``separators`` parameter at all -- passing it
    raises a ``TypeError`` -- and its own "compact" formatting has not
    historically been guaranteed to match stdlib's ``(",", ":")`` output
    byte-for-byte (some versions insert a space after ``,`` or ``:``).
    ``ujson`` has also had version-dependent quirks around ``sort_keys``
    support and around how NaN/Infinity/float values are encoded compared
    to stdlib ``json`` (which emits the non-standard tokens ``NaN``,
    ``Infinity``, ``-Infinity``).

    To bridge this, the wrapper:

    1. Walks ``record`` up front and rejects (via ``ValueError``) any
       NaN/Infinity float values, since their cross-version ``ujson``
       encoding is not guaranteed to be stable/deterministic relative to
       the previous stdlib-``json``-based audit log format.
    2. Calls ``ujson.dumps(record, sort_keys=sort_keys)`` (omitting the
       unsupported ``separators`` argument).
    3. Post-processes the resulting string, stripping any whitespace that
       immediately follows a ``,`` or ``:`` which lies outside of a JSON
       string literal, so the final text exactly matches the compact
       ``(",", ":")`` separator convention used by the rest of this
       module.

    This keeps audit log records deterministic and byte-reproducible
    across the ``json`` -> ``ujson`` backend swap.
    """
    def _check(value):
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(
                    "unsupported float value for deterministic JSON "
                    "audit log: %r" % (value,)
                )
        elif isinstance(value, dict):
            for v in value.values():
                _check(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _check(v)

    _check(record)

    raw = ujson.dumps(record, sort_keys=sort_keys)

    out_chars = []
    in_string = False
    escape = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        out_chars.append(ch)
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
                j = i + 1
                while j < n and raw[j] in " \t\n\r":
                    j += 1
                i = j - 1
        i += 1
    return "".join(out_chars)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
