# CHOSEN-TARGET: first-party
"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON

Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")

_ESCAPE_MAP = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _encode_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESCAPE_MAP:
            out.append(_ESCAPE_MAP[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
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


def _encode_value(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return _encode_string(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _encode_float(v)
    if isinstance(v, Mapping):
        return _encode_dict(v)
    if isinstance(v, (list, tuple)):
        return _encode_list(v)
    raise TypeError(f"Object of type {type(v).__name__} is not JSON serializable")


def _encode_list(lst) -> str:
    item_sep, _ = _SEPARATORS
    return "[" + item_sep.join(_encode_value(item) for item in lst) + "]"


def _encode_dict(d: Mapping) -> str:
    item_sep, kv_sep = _SEPARATORS
    parts = []
    for key in sorted(d.keys()):
        if not isinstance(key, str):
            key_str = str(key)
        else:
            key_str = key
        parts.append(_encode_string(key_str) + kv_sep + _encode_value(d[key]))
    return "{" + item_sep.join(parts) + "}"


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _encode_dict(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
