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


def _iter_floats_in_sorted_order(obj):
    """Yield every float value contained in `obj`, in the exact order it will
    appear in the JSON text produced by ujson.dumps(obj, sort_keys=True).

    Dict keys are visited in sorted order (mirroring `sort_keys=True`) and
    list/tuple elements are visited in their existing order, recursing into
    nested containers. This lets the wrapper below line up each float in the
    already-serialized text with the original Python float object it came
    from, so it can be re-rendered with Python's own repr().
    """
    if isinstance(obj, dict):
        for key in sorted(obj.keys()):
            yield from _iter_floats_in_sorted_order(obj[key])
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_floats_in_sorted_order(item)
    elif isinstance(obj, float):
        yield obj


def _canonical_ujson_dumps_wrapper(record):
    """Serialize `record` the way stdlib json.dumps(record, sort_keys=True,
    separators=(',', ':')) would, using ujson as the backend.

    Why this wrapper exists
    -----------------------
    stdlib `json.dumps` accepts a `separators` keyword so callers can force
    compact ",", ":" separators between items/keys with no extra whitespace.
    `ujson.dumps` has no `separators` parameter at all -- passing it raises a
    TypeError -- because ujson always emits its own fixed, already-compact
    formatting (no spaces after ',' or ':'). That fixed formatting is
    identical to what `separators=(',', ':')` produces, so it is safe to
    simply omit the keyword when calling ujson.

    `ujson.dumps` also has no `double_precision` keyword in some builds (and
    even where it exists, it forces a *fixed* number of decimal places, e.g.
    1.5 -> "1.5000000000") -- neither behaviour matches stdlib json, which
    renders floats using the shortest round-trip repr() (1.5 -> "1.5"). To
    stay byte-for-byte identical to stdlib json without depending on that
    unsupported/unreliable keyword, we call `ujson.dumps` with no float
    precision option at all, then walk the *original* `record` (not ujson's
    output) in the same sorted-key/list order ujson used to find every float
    value, and substitute each numeric-looking substring in the raw output,
    in order, with Python's own `repr()` of that original float. This
    recovers exact stdlib-style float formatting regardless of what
    precision ujson happened to use internally.

    Non-ASCII escaping and key ordering are unaffected: both libraries
    default to `ensure_ascii=True` and both honor `sort_keys=True` the same
    way, so those are passed straight through with no post-processing.

    One more divergence: `ujson.dumps` escapes forward-slash characters as
    '\\/' in its output, while stdlib `json.dumps` never escapes '/' (it has
    no such escaping by default). Left alone, this would corrupt any string
    value containing '/' (e.g. tag names like "a/b"). After the float
    substitution above, we therefore replace every '\\/' in the raw ujson
    output with a plain '/' to match stdlib json's default behaviour.

    The result is a string byte-identical to
    `json.dumps(record, sort_keys=True, separators=(',', ':'))`.
    """
    raw = ujson.dumps(record, sort_keys=True, ensure_ascii=True)

    floats = iter(_iter_floats_in_sorted_order(record))

    def _restore_float(match: "re.Match") -> str:
        return repr(next(floats))

    substituted = re.sub(r"-?\d+\.\d+(?:[eE][+-]?\d+)?", _restore_float, raw)
    return substituted.replace("\\/", "/")


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _canonical_ujson_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
