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
    """Serialize ``record`` to a JSON string with byte-for-byte stdlib-``json``
    determinism, using ``ujson`` as the underlying encoder.

    stdlib ``json.dumps(record, sort_keys=True, separators=(",", ":"))``
    produces a compact, deterministic string: keys sorted, no whitespace
    around ``,`` and ``:``, and strict values for special floats (raising
    ``ValueError`` on NaN/Infinity unless ``allow_nan=True``, in which case it
    emits the bare tokens ``NaN``, ``Infinity``, ``-Infinity``).

    ``ujson.dumps`` does support a ``sort_keys`` flag, but its handling of
    the ``separators`` argument is inconsistent/partial across versions (it
    may ignore custom separators or only partially honor them, sometimes
    emitting a space after ``:`` or ``,``), and it silently encodes NaN/
    Infinity as the bare tokens instead of raising, which would make audit
    logs non-deterministic/non-portable relative to stdlib ``json`` output.

    This wrapper calls ``ujson.dumps`` for the fast encode, then
    post-processes the resulting string to: (1) strip any stray whitespace
    ujson may have inserted around the ``,`` and ``:`` separators so the
    output exactly matches the requested compact ``separators`` tuple, and
    (2) detect and reject (via ``ValueError``) any bare ``NaN``/``Infinity``/
    ``-Infinity`` tokens in the output, matching stdlib ``json``'s default
    strict behavior so unsupported special float values cannot silently
    corrupt deterministic audit logs.
    """
    raw = ujson.dumps(record, sort_keys=sort_keys)

    item_sep, key_sep = separators

    normalized_chars = []
    in_string = False
    escape = False
    i = 0
    length = len(raw)
    while i < length:
        ch = raw[i]
        if in_string:
            normalized_chars.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            normalized_chars.append(ch)
            i += 1
            continue

        if ch == ",":
            normalized_chars.append(item_sep)
            i += 1
            while i < length and raw[i] == " ":
                i += 1
            continue

        if ch == ":":
            normalized_chars.append(key_sep)
            i += 1
            while i < length and raw[i] == " ":
                i += 1
            continue

        if ch in "NI" and not in_string:
            for token in ("NaN", "Infinity", "-Infinity"):
                if raw.startswith(token, i):
                    raise ValueError(
                        "Out of range float values are not JSON compliant: "
                        + token
                    )

        normalized_chars.append(ch)
        i += 1

    return "".join(normalized_chars)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
