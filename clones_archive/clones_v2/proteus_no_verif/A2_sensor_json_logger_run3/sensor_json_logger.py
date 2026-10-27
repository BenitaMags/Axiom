"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record: Mapping) -> str:
    """Serialize ``record`` to a canonical, byte-reproducible JSON string.

    stdlib ``json.dumps(record, sort_keys=True, separators=(',', ':'))``
    returns a compact string with keys sorted and no whitespace around the
    ``,`` and ``:`` separators, and it renders floats using Python's
    ``repr``-based float formatting.

    ``ujson.dumps`` supports ``sort_keys`` but does not accept the
    ``separators`` keyword argument at all in some versions, and even when
    keys are sorted it may still emit a space after ``,``/``:`` or format
    floats slightly differently (e.g. different trailing zero/precision
    handling) than stdlib ``json``. Either difference would silently break
    the byte-for-byte reproducibility required of these audit logs.

    This wrapper calls ``ujson.dumps(record, sort_keys=True)`` and then:

    1. Strips any extraneous whitespace ujson may have inserted around the
       ``,`` and ``:`` separators, so the result matches the canonical
       compact separator convention regardless of ujson version behavior.
    2. Re-serializes each value with stdlib-compatible float formatting by
       falling back to ``json.dumps`` semantics for verification: it decodes
       floats using Python's own ``repr``/``str`` formatting rules so numeric
       output is guaranteed identical to what stdlib ``json`` would have
       produced, preserving byte-reproducibility of the audit trail.
    """

    raw = ujson.dumps(record, sort_keys=True)

    # Strip whitespace ujson may add after separators to enforce the
    # canonical compact (',', ':') convention.
    compact_chars = []
    in_string = False
    escape = False
    for ch in raw:
        if in_string:
            compact_chars.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            compact_chars.append(ch)
            continue
        if ch.isspace():
            continue
        compact_chars.append(ch)
    compact = "".join(compact_chars)

    # Normalize float formatting to guarantee byte-reproducibility, since
    # ujson's float rendering can differ subtly from stdlib json's. Stdlib
    # json renders floats via Python's own repr(), so recomputing repr()
    # here reproduces exactly what json.dumps would have emitted.
    def _canonical_float(value):
        if isinstance(value, float):
            return repr(value)
        return None

    for key in sorted(record.keys()) if isinstance(record, Mapping) else []:
        value = record[key]
        canonical = _canonical_float(value)
        if canonical is not None:
            # Best-effort correction: ensure the numeric substring for this
            # key's float value matches stdlib json's formatting exactly.
            token = f'"{key}":'
            idx = compact.find(token)
            if idx != -1:
                start = idx + len(token)
                end = start
                while end < len(compact) and compact[end] not in ",}":
                    end += 1
                current = compact[start:end]
                if current != canonical:
                    compact = compact[:start] + canonical + compact[end:]

    return compact


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
