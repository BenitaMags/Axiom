"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_compact_json(s: str) -> str:
    """Strip the single space ujson inserts after top-level ',' and ':'
    delimiters, while leaving any spaces that occur inside string literals
    untouched. This is a small hand-rolled scanner (not a regex) so that it
    is safe against string values that happen to contain ", " or ": ".
    """
    result = []
    in_string = False
    escape = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        result.append(c)
        i += 1
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c in (",", ":") and i < n and s[i] == " ":
                i += 1  # drop the extra space ujson inserted
    return "".join(result)


def _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Bridge json.dumps(record, sort_keys=True, separators=(',', ':')) to
    ujson.

    json.dumps with sort_keys=True and separators=(',', ':') produces a
    canonical, compact string: dict keys in sorted order, no space after a
    comma or colon. ujson.dumps has no `separators` parameter at all -- it
    always renders with a space after ',' and ':' -- and its `sort_keys`
    support has varied across releases (sometimes ignored). Passing either
    kwarg straight through to ujson would either raise TypeError or
    silently produce non-canonical, non-reproducible output, which breaks
    this module's whole purpose (byte-identical audit records).

    Additionally, ujson.dumps escapes forward slashes ('/' -> '\\/') by
    default, while json.dumps never escapes them. Left uncorrected, any
    string value containing '/' (e.g. a tag name "a/b") would round-trip to
    a different byte sequence than the original json-based output. This
    wrapper passes escape_forward_slashes=False to suppress that escaping.

    To reproduce the original behaviour exactly, this wrapper:
      1. Recursively rebuilds every dict in `record` with keys inserted in
         sorted order, since ujson's own sort_keys support cannot be
         trusted across versions.
      2. Calls ujson.dumps on the pre-sorted structure with
         escape_forward_slashes=False (to match json's non-escaping of '/')
         and without any separators kwarg (ujson does not accept one).
      3. Post-processes the resulting string with `_axiom_compact_json` to
         remove the extra spaces ujson inserts after ',' and ':', which is
         the only way to get compact output since ujson exposes no
         separator-control flags.

    The `separators` argument is accepted only for call-site compatibility;
    this wrapper always produces the equivalent of separators=(',', ':').
    """
    def _sort(obj):
        if isinstance(obj, dict):
            return {k: _sort(obj[k]) for k in sorted(obj.keys())}
        if isinstance(obj, (list, tuple)):
            return [_sort(v) for v in obj]
        return obj

    data = _sort(record) if sort_keys else record
    encoded = ujson.dumps(data, escape_forward_slashes=False)
    return _axiom_compact_json(encoded)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
