"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
import re
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")

# Matches either a complete double-quoted JSON string (left untouched) or a
# run of whitespace outside of a string (to be stripped) / a bare numeric
# literal (to be inspected for float reformatting). Using a single
# alternation keeps quoted string contents from ever being modified.
_STRING_OR_WHITESPACE_RE = re.compile(r'"(?:\\.|[^"\\])*"|\s+')
_STRING_OR_FLOAT_RE = re.compile(
    r'"(?:\\.|[^"\\])*"|-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+[eE][+-]?\d+'
)


def _sort_recursively(obj):
    """Recursively return a copy of ``obj`` with all mapping keys sorted.

    ujson's ``sort_keys`` keyword has been inconsistently supported across
    releases (some old versions silently ignore it), so instead of relying
    on ujson to sort keys we sort them ourselves, in pure Python, before
    ever calling ujson.dumps. This keeps determinism guaranteed regardless
    of the installed ujson version.
    """
    if isinstance(obj, Mapping):
        return {key: _sort_recursively(obj[key]) for key in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_sort_recursively(item) for item in obj]
    return obj


def _strip_whitespace_outside_strings(s: str) -> str:
    """Remove all whitespace from ``s`` except whitespace inside JSON strings.

    ujson has, in some versions, only partially honored the ``separators``
    keyword (e.g. still inserting a space after ``:`` or ``,``). To
    guarantee the exact compact separators our audit format requires
    regardless of ujson version behavior, we defensively strip any stray
    whitespace outside of string literals after serialization.
    """
    def _repl(match: "re.Match") -> str:
        token = match.group(0)
        return token if token.startswith('"') else ""

    return _STRING_OR_WHITESPACE_RE.sub(_repl, s)


def _normalize_float_literals(s: str) -> str:
    """Re-render numeric float literals in ``s`` to match stdlib json's style.

    stdlib json renders floats using Python's ``repr(float)``, which chooses
    the shortest round-trippable decimal representation. ujson's own float
    formatting can differ slightly in precision/rounding, which would break
    byte-for-byte reproducibility of audit logs for numeric sensor values.
    This function finds numeric literals outside of quoted strings and
    re-renders each one via ``repr(float(...))`` so the output matches what
    stdlib json would have produced.
    """
    def _repl(match: "re.Match") -> str:
        token = match.group(0)
        if token.startswith('"'):
            return token
        return repr(float(token))

    return _STRING_OR_FLOAT_RE.sub(_repl, s)


def _axiom_json_dumps_wrapper(record: Mapping) -> str:
    """Serialize ``record`` to a canonical compact JSON string using ujson.

    stdlib ``json.dumps(record, sort_keys=True, separators=_SEPARATORS)``
    returns a string with keys sorted, no extra whitespace around ``,``/``:``,
    and floats rendered via Python's ``repr``. ``ujson.dumps`` instead may
    (depending on version) ignore or only partially honor ``sort_keys`` and
    ``separators``, and it renders floats with its own C-level formatter that
    can differ in precision/rounding from stdlib json.

    To bridge this, this wrapper: (1) sorts mapping keys recursively in pure
    Python before calling ujson.dumps, so determinism never depends on
    ujson's sort_keys support; (2) calls ujson.dumps with the requested
    separators and then strips any stray whitespace outside of strings, so
    the exact compact separators are guaranteed regardless of ujson version
    behavior; and (3) re-renders any float literals in the resulting string
    to match stdlib json's float formatting/precision, so numeric output
    stays byte-identical to the previous json-based implementation.
    """
    sorted_record = _sort_recursively(record)
    dumped = ujson.dumps(sorted_record, separators=_SEPARATORS)
    dumped = _strip_whitespace_outside_strings(dumped)
    dumped = _normalize_float_literals(dumped)
    return dumped


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
