"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _sort_keys_recursive(obj):
    """Recursively rebuild dicts with keys inserted in sorted order.

    ujson's `sort_keys` support has historically been inconsistent/partial
    across versions (sometimes ignored, sometimes only applied at the top
    level), so we cannot rely on passing sort_keys=True through to ujson.
    Instead we walk the structure ourselves and produce a new object where
    every mapping's keys are inserted in sorted order. Since Python dicts
    preserve insertion order, serializing this rebuilt structure yields the
    same deterministic key ordering that stdlib `json.dumps(sort_keys=True)`
    would produce.
    """
    if isinstance(obj, Mapping):
        return {k: _sort_keys_recursive(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_sort_keys_recursive(v) for v in obj]
    return obj


def _ujson_deterministic_dumps(record, sort_keys=True, separators=_SEPARATORS):
    """Wrapper bridging json.dumps(record, sort_keys=True, separators=(",", ":"))
    onto ujson's API.

    stdlib `json.dumps` accepts `sort_keys` (recursively sorts all nested
    mapping keys) and a fully customizable `separators` tuple (item
    separator, key separator) to control whitespace in the output. ujson
    instead: (a) has unreliable/partial support for `sort_keys` depending on
    version, and (b) does not accept an arbitrary `separators` tuple at all
    -- it always emits its own compact representation (no spaces around
    ':' or ',' by default), which already matches the `(",", ":")`
    separators this file requires for byte-reproducible audit records.

    Additionally, stdlib `json.dumps` does NOT escape forward slashes ('/')
    by default, but ujson's default behaviour DOES escape them (emitting
    '\\/' for '/'). That non-standard escaping would silently change the
    byte content of any record containing a '/' (e.g. a tag name like
    "a/b"), breaking byte-reproducibility with the original stdlib output.
    We pass `escape_forward_slashes=False` to ujson.dumps to suppress this
    and match stdlib json's default (unescaped) behaviour.

    This wrapper therefore: recursively pre-sorts all mapping keys itself
    (see `_sort_keys_recursive`) before handing the structure to ujson,
    silently ignores/drops the `separators` kwarg rather than forwarding it
    to ujson (which would raise a TypeError), relying on ujson's default
    output already being compact, and disables ujson's non-standard forward
    slash escaping so the output matches stdlib json byte-for-byte. This
    preserves the deterministic, byte-reproducible output the audit log
    format depends on.
    """
    if sort_keys:
        record = _sort_keys_recursive(record)
    return ujson.dumps(record, escape_forward_slashes=False)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _ujson_deterministic_dumps(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
