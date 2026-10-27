"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record):
    """Bridge json.dumps(record, sort_keys=True, separators=(',', ':')) to ujson.

    stdlib `json.dumps(..., separators=(',', ':'))` produces a canonical,
    compact JSON string: no whitespace after ',' or ':' and float values
    rendered via Python's `repr(float)` rules (shortest round-tripping
    representation, e.g. "1.0", "3.14").

    `ujson.dumps` does not reliably honor a custom `separators` argument
    (it falls back to its own internal compact-ish format), its float
    formatting can differ from stdlib json in precision/trailing zeros,
    and by default it escapes forward slashes as backslash-slash, which
    stdlib json never does. Each of these is a hard requirement here
    because these are audit log records that must be byte-reproducible.

    This wrapper calls ujson.dumps(record, sort_keys=True,
    escape_forward_slashes=False) -- the flag restores stdlib's
    never-escape-slashes behavior -- and then post-processes the
    resulting text with a small string-literal-aware scanner that
    (1) strips any whitespace that appears outside of quoted strings,
    guaranteeing the exact ',' / ':' compact separator convention, and
    (2) re-renders every bare numeric token outside of quoted strings
    using Python's own int()/float() + repr() formatting, so numbers
    match what stdlib json.dumps would have produced.
    """
    raw = ujson.dumps(record, sort_keys=True, escape_forward_slashes=False)
    return _normalize_json_text(raw)


def _normalize_json_text(text):
    """Normalize a ujson-produced JSON string to match stdlib json output.

    Walks the string tracking whether we are inside a quoted JSON string
    (respecting backslash escapes) so that whitespace-stripping and number
    reformatting are only applied to *structural* JSON text, never to the
    contents of string values.
    """
    out = []
    in_string = False
    escape = False
    number_buf: list[str] = []

    def flush_number():
        if number_buf:
            out.append(_normalize_number("".join(number_buf)))
            number_buf.clear()

    for ch in text:
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            flush_number()
            in_string = True
            out.append(ch)
            continue

        if ch in "0123456789+-.eE":
            number_buf.append(ch)
            continue

        flush_number()

        if ch.isspace():
            continue

        out.append(ch)

    flush_number()
    return "".join(out)


def _normalize_number(token):
    """Re-render a bare numeric token using stdlib int/float + repr rules.

    ujson may emit floats with different precision or trailing-zero
    conventions than stdlib json (which uses `float.__repr__`). Re-parsing
    the token with Python's own numeric types and re-rendering it makes
    the output byte-identical to what json.dumps would have produced.
    """
    try:
        if any(c in token for c in ".eE"):
            return repr(float(token))
        return str(int(token))
    except ValueError:
        return token


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
