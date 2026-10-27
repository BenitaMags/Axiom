"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _compact_json_separators(text: str) -> str:
    """Strip whitespace following ',' or ':' outside of JSON string literals.

    Some ujson versions insert a space after ',' and/or ':' even in their
    most compact output mode (ujson.dumps has no 'separators' kwarg at all,
    unlike json.dumps). json.dumps(..., separators=(',', ':')) never emits
    such whitespace. This scans the serialized text, tracking whether we are
    inside a quoted JSON string (respecting backslash escapes) so we don't
    touch whitespace that is part of an actual string value, and removes any
    whitespace immediately following a structural ',' or ':' character. The
    result matches json.dumps' compact-separator output byte-for-byte.
    """
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        out.append(ch)
        if not in_string:
            if ch == '"':
                in_string = True
            elif ch in (",", ":"):
                j = i + 1
                while j < n and text[j] in " \t\n\r":
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


def _axiom_json_dumps_wrapper(record, sort_keys: bool = True) -> str:
    """Bridge json.dumps(..., separators=(',', ':')) semantics onto ujson.

    json.dumps(record, sort_keys=True, separators=(',', ':')) returns a
    compact, deterministically key-sorted JSON string with no whitespace
    around separators, stable float formatting (via repr), and raises
    json.JSONDecodeError / TypeError on encoding failures.

    ujson.dumps has no 'separators' parameter at all -- it is accepted here
    as simply unsupported and omitted from the call -- and depending on
    version may include a space after ',' or ':' in its output. ujson has
    also historically differed from json in float precision/representation
    and in the exception types raised for unsupported values (e.g. raising
    TypeError/OverflowError/ValueError rather than something equivalent to
    json.JSONDecodeError).

    This wrapper calls ujson.dumps(record, sort_keys=sort_keys), then runs
    the result through _compact_json_separators() to strip any whitespace
    ujson may have inserted after structural ',' or ':' characters, so the
    returned string is byte-identical to what
    json.dumps(record, sort_keys=True, separators=(',', ':')) would produce.
    Any exception raised during serialization is normalized to TypeError so
    callers depending on json's error-handling contract keep working.
    """
    try:
        raw = ujson.dumps(record, sort_keys=sort_keys)
    except Exception as exc:
        raise TypeError(f"Unable to serialize record to JSON: {exc}") from exc
    _spt_r2 = _compact_json_separators(raw)

    return _spt_r2


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    _spt_r3 = _axiom_json_dumps_wrapper({"tag": tag, "value": value, "ts": ts, "quality": quality}, sort_keys=True)
    return _spt_r3


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    _spt_r4 = "\n".join(format_reading(**dict(r)) for r in readings)
    return _spt_r4
