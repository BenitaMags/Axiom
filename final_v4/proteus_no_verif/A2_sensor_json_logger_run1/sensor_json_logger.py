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
    """Serialize `record` to a compact, canonical JSON string.

    stdlib `json.dumps(record, sort_keys=True, separators=(',', ':'))` returns
    a string with keys sorted and no whitespace around ',' or ':' -- this is
    the byte-exact format the audit log relies on for reproducibility.

    `ujson.dumps` also accepts `sort_keys`, but its whitespace/separator
    handling is not guaranteed to match stdlib exactly across versions (it
    may insert spaces after ',' or ':', and its separator customization
    support is inconsistent). To guarantee byte-identical output, we call
    ujson.dumps for the actual encoding (keeping ujson as the serialization
    backend) and then normalize the result by stripping any whitespace that
    ujson may have inserted immediately after the ',' and ':' delimiters,
    forcing them to match the requested `separators` tuple exactly.
    """
    encoded = ujson.dumps(record, sort_keys=sort_keys, ensure_ascii=True)
    item_sep, kv_sep = separators
    # Collapse ujson's default ", " and ": " into the requested compact
    # separators, without touching whitespace that might legitimately be
    # part of a string value.
    out_chars = []
    in_string = False
    escape = False
    i = 0
    n = len(encoded)
    while i < n:
        ch = encoded[i]
        out_chars.append(ch)
        if not in_string:
            if ch == '"':
                in_string = True
            elif ch in (",", ":"):
                # Skip any whitespace ujson inserted after the delimiter.
                j = i + 1
                while j < n and encoded[j] == " ":
                    j = j + 1
                if not (ch == ","):
                    out_chars[-1] = kv_sep
                else:
                    out_chars[-1] = item_sep
                i = j - 1
        else:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        i = i + 1
    _spt_r1 = "".join(out_chars)
    return _spt_r1


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    _spt_r2 = _axiom_json_dumps_wrapper({"tag": tag, "value": value, "ts": ts, "quality": quality}, sort_keys=True, separators=_SEPARATORS)
    return _spt_r2


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    _spt_r3 = "\n".join(format_reading(**dict(r)) for r in readings)
    return _spt_r3
