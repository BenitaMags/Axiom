"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _strip_json_whitespace(raw: str) -> str:
    """Remove whitespace after ',' and ':' tokens that lies outside strings.

    Helper used by ``_axiom_json_dumps_wrapper``. Implemented as a small
    state machine (rather than a regex or blind string replace) so that any
    literal whitespace occurring *inside* a JSON string value is left
    untouched -- only the structural separator whitespace is trimmed, the
    same whitespace that ``json.dumps(..., separators=(",", ":"))`` would
    never have emitted in the first place.
    """
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        out.append(ch)
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
                while j < n and raw[j] in (" ", "\t", "\n", "\r"):
                    j += 1
                i = j
                continue
        i += 1
    return "".join(out)


def _axiom_json_dumps_wrapper(record) -> str:
    """Serialize ``record`` to a canonical, compact JSON string.

    stdlib ``json.dumps`` was called here as
    ``json.dumps(record, sort_keys=True, separators=(",", ":"))``: the
    ``separators`` kwarg forces compact, whitespace-free output (no space
    after ``,`` or ``:``) which, combined with ``sort_keys=True``, gives the
    byte-reproducible records this module's audit logs require. stdlib json
    also never escapes literal ``/`` characters -- a ``/`` in a string value
    is emitted as-is.

    ``ujson.dumps`` returns a ``str`` (like stdlib json, unlike some other
    fast-JSON libraries that return ``bytes``), and supports ``sort_keys``,
    but its support for a ``separators`` kwarg is inconsistent/unreliable
    across versions, so we never pass it. Instead we call
    ``ujson.dumps(record, sort_keys=True, escape_forward_slashes=False)`` --
    the ``escape_forward_slashes=False`` flag is required because ujson, by
    default, escapes every ``/`` in string values as ``\\/``, which stdlib
    json never does; without this flag records containing tags/values with
    a literal ``/`` would not byte-match the original output. The result is
    already compact in modern ujson versions, and we then defensively
    post-process it with ``_strip_json_whitespace`` to strip any separator
    whitespace that a given ujson version might still emit. This reproduces
    stdlib json's exact ``separators=(",", ":")`` formatting (including
    float repr, which ujson leaves as plain decimal/scientific text tokens
    untouched by the whitespace pass) so downstream audit-log
    byte-reproducibility holds regardless of the installed ujson version.
    """
    import ujson  # connector: bound to wrapper scope
    raw = ujson.dumps(record, sort_keys=True, escape_forward_slashes=False)
    return _strip_json_whitespace(raw)



def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
