# CHOSEN-TARGET: first-party
"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON

Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _encode_string(s: str) -> str:
    """Encode a string as a JSON string literal, matching json.dumps default
    behaviour (ensure_ascii=True)."""
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\b':
            out.append('\\b')
        elif ch == '\f':
            out.append('\\f')
        elif cp < 0x20:
            out.append('\\u%04x' % cp)
        elif cp < 0x7F:
            out.append(ch)
        else:
            if cp > 0xFFFF:
                cp -= 0x10000
                hi = 0xD800 + (cp >> 10)
                lo = 0xDC00 + (cp & 0x3FF)
                out.append('\\u%04x\\u%04x' % (hi, lo))
            else:
                out.append('\\u%04x' % cp)
    out.append('"')
    return "".join(out)


def _encode_float(f: float) -> str:
    if f != f:
        return "NaN"
    if f == float("inf"):
        return "Infinity"
    if f == float("-inf"):
        return "-Infinity"
    return repr(f)


def _encode_value(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _encode_float(value)
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda kv: kv[0])
        parts = [
            f"{_encode_string(str(k))}:{_encode_value(v)}" for k, v in items
        ]
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        parts = [_encode_value(v) for v in value]
        return "[" + ",".join(parts) + "]"
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )


def _dumps_sorted(obj: dict) -> str:
    items = sorted(obj.items(), key=lambda kv: kv[0])
    parts = [f"{_encode_string(str(k))}:{_encode_value(v)}" for k, v in items]
    return "{" + ",".join(parts) + "}"


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _dumps_sorted(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
