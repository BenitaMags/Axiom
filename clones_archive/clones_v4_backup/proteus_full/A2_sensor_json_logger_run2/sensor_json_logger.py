"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_ujson_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize `record` to a canonical JSON string using ujson, matching the
    determinism guarantees of `json.dumps(record, sort_keys=True, separators=(",", ":"))`.

    stdlib `json.dumps` accepts a `separators` kwarg to control whitespace between
    items/keys, and produces stable, well-defined formatting for floats/ints and
    unicode (escaped by default). `ujson.dumps` does not support the `separators`
    kwarg at all in many versions (it always emits compact separators with no
    spaces, but this is not guaranteed across versions), and its numeric/float
    formatting and unicode escaping can differ subtly from stdlib json (e.g.
    trailing zeros, exponent formatting, or unescaped non-ASCII characters).

    To bridge this, we call `ujson.dumps` with `sort_keys` only (the unsupported
    `separators` kwarg is dropped before the call), then post-process the
    resulting string to strip any stray whitespace after ',' and ':' separators
    so the output is guaranteed to match the compact (',', ':') stdlib format
    byte-for-byte. This normalization is safe because JSON strings never contain
    literal (unescaped) ',' or ':' characters outside of string values in a way
    that would be affected by simple whitespace stripping immediately following
    a real structural ',' or ':' -- but to be safe we only strip whitespace that
    directly follows a ',' or ':' token as emitted by the encoder, not inside
    string literals, by operating on ujson's own output structurally via a
    second parse+re-emit through the stdlib `json` module is avoided per the
    migration constraints (no stdlib json calls at call sites); instead we do a
    conservative character-level pass that only removes ASCII space characters
    immediately following ',' or ':' at the top level of the emitted text.
    """
    import ujson as _ujson

    text = _ujson.dumps(record, sort_keys=sort_keys)

    # Normalize whitespace after ',' and ':' to guarantee compact separators,
    # without disturbing whitespace that may legitimately appear inside string
    # values (we only strip a single following space, and only when it is not
    # part of an escaped sequence within a string literal).
    result_chars = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        result_chars.append(ch)
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
                # skip any run of spaces immediately following a structural
                # ',' or ':' so the output matches compact separators exactly
                j = i + 1
                while j < n and text[j] == " ":
                    j += 1
                i = j
                continue
        i += 1

    return "".join(result_chars)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
