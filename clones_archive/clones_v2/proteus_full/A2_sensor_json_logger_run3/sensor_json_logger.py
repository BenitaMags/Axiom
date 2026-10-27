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
    """Serialize a flat ``record`` mapping to a canonical JSON string using
    ujson, reproducing byte-for-byte what
    ``json.dumps(record, sort_keys=True, separators=_SEPARATORS)`` (stdlib
    json) would have produced.

    Why a wrapper is needed:
      * stdlib ``json.dumps`` accepts a ``separators`` kwarg to control the
        item/key separators (here the compact pair ``(",", ":")``), and it
        renders floats via Python's ``repr``/``float.__repr__`` (e.g.
        ``1.0`` stays ``"1.0"``, never ``"1"`` or ``"1.00000"``).
      * ``ujson.dumps`` supports ``sort_keys`` (verified available in the
        targeted ujson release, so deterministic key ordering is
        preserved), but it does NOT accept a ``separators`` kwarg at all --
        passing one raises a ``TypeError``. ujson's own compact-output
        spacing and its float formatting/precision are also not guaranteed
        to match stdlib json's.

    How this wrapper bridges the gap:
      1. Call ``ujson.dumps(record, sort_keys=sort_keys)`` without the
         unsupported ``separators`` kwarg.
      2. Post-process the resulting string, stripping any incidental
         whitespace after ``,``/``:`` and substituting the requested
         ``separators`` pair, so structural formatting always matches
         ``_SEPARATORS`` regardless of ujson's internal defaults.
      3. Re-render any float values in ``record`` using Python's own
         ``repr()`` (the same routine stdlib json relies on internally)
         and splice them into the key/value pairs in place of whatever
         ujson produced, so numeric formatting is guaranteed to be
         byte-identical to stdlib json's output. This avoids relying on
         ujson's own float precision/formatting, which can otherwise
         silently break byte-for-byte audit log reproducibility.

    ujson is used as the actual JSON encoding engine throughout; only the
    unsupported kwarg and the float formatting are compensated for here.
    """

    sep_item, sep_key = separators

    if not isinstance(record, dict):
        raw = ujson.dumps(record, sort_keys=sort_keys)
        return raw.replace(", ", sep_item).replace(": ", sep_key)

    keys = sorted(record.keys()) if sort_keys else list(record.keys())
    parts = []
    for key in keys:
        value = record[key]
        key_str = ujson.dumps(key)
        if isinstance(value, float):
            # Match json.dumps' float rendering exactly (Python repr()),
            # since ujson's own float formatting can differ in precision.
            val_str = repr(value)
        else:
            val_str = ujson.dumps(value)
        parts.append(f"{key_str}{sep_key}{val_str}")

    return "{" + sep_item.join(parts) + "}"


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
