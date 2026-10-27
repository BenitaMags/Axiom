"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
from typing import Iterable, Mapping

from json_text_normalizer import normalize_json_text

_SEPARATORS = (",", ":")


def _axiom_normalize_json_text(text, separators):
    """Rewrite ``text`` (a compact-ish JSON document with no embedded
    newlines) so structural separators exactly match ``separators`` and all
    whitespace outside of string literals is removed, and so every float
    literal is reformatted via ``repr(float(...))`` to match stdlib json's
    deterministic float formatting. Used to make ``ujson.dumps`` output
    byte-identical to stdlib ``json.dumps`` output.
    """
    return normalize_json_text(text, separators)


def _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize ``record`` to a JSON string with the same guarantees stdlib
    ``json.dumps(record, sort_keys=True, separators=(",", ":"))`` gave us.

    stdlib ``json.dumps`` accepts a ``separators`` keyword and controls
    numeric formatting via Python's ``float.__repr__``, producing a fixed,
    compact, byte-reproducible string (no stray whitespace, deterministic
    float text like ``1.0`` / ``1e+100``).

    ``ujson.dumps`` supports ``sort_keys`` but its handling of ``separators``
    is inconsistent across versions (it may ignore the kwarg entirely), and
    its numeric formatting for floats/large ints can diverge slightly from
    stdlib json (different trailing zeros, exponent casing, etc.). Passing
    ``separators`` straight through is therefore unreliable.

    This wrapper calls ``ujson.dumps`` with only ``sort_keys`` (letting
    ujson pick whatever separator style it wants), then walks the resulting
    text -- skipping over string literals so we never touch user data --
    and: (1) rewrites every structural ',' and ':' to use the requested
    compact separators with no trailing whitespace, and (2) re-renders every
    numeric literal that looks like a float (contains '.', 'e' or 'E')
    through Python's own ``repr(float(...))`` so the digits match exactly
    what stdlib ``json.dumps`` would have emitted. Integers are left as-is
    since their textual form can't diverge. This keeps the audit log
    byte-reproducible regardless of the ujson version's quirks.
    """
    import ujson

    encoded = ujson.dumps(record, sort_keys=sort_keys)
    return normalize_json_text(encoded, separators)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
