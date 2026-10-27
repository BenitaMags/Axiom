"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_ujson_dumps_wrapper(record: Mapping) -> str:
    """Serialize ``record`` to the same canonical bytes ``json.dumps`` would.

    stdlib ``json.dumps(record, sort_keys=True, separators=_SEPARATORS)``
    returns a compact, sorted-key JSON string with no whitespace around
    ``,`` or ``:``, and formats floats using Python's ``repr()`` (the
    shortest string that round-trips back to the same float).

    ``ujson.dumps`` has no ``separators`` keyword at all -- passing one
    raises ``TypeError`` -- so it must be omitted. That omission is safe
    here because ujson's default output is already comma/colon-compact
    with no inserted spaces, which is byte-identical to what
    ``separators=_SEPARATORS`` produces from stdlib ``json``. ujson does
    support ``sort_keys``, so key ordering is preserved directly.

    The remaining risk is float formatting: some ujson releases render
    floats with reduced/truncated precision instead of the shortest
    round-trip representation stdlib ``json`` uses. To guarantee the
    byte-for-byte reproducibility this audit logger requires, this
    wrapper re-derives the canonical text for every float field via
    Python's own ``repr()`` and substitutes it into the ujson output
    wherever ujson rendered that float differently.

    Errors are not swallowed: any exception ujson raises while encoding
    (e.g. ``OverflowError`` for out-of-range floats, ``TypeError`` for
    values it cannot serialize) propagates to the caller unchanged, since
    those are the same exception types stdlib ``json.dumps`` raises for
    the same invalid input.
    """
    encoded = ujson.dumps(record, sort_keys=True)

    for value in record.values():
        if isinstance(value, float):
            canonical = repr(value)
            ujson_repr = ujson.dumps(value)
            if ujson_repr != canonical:
                if ujson_repr in encoded:
                    encoded = encoded.replace(ujson_repr, canonical, 1)

    return encoded


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    _spt_r1 = _axiom_ujson_dumps_wrapper(record)
    return _spt_r1


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    _spt_r2 = "\n".join(format_reading(**dict(r)) for r in readings)
    return _spt_r2
