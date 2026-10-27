"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record) -> str:
    """Serialize `record` to a compact, canonical JSON string using ujson.

    Background: stdlib `json.dumps(record, sort_keys=True, separators=(",", ":"))`
    produces a deterministic, compact one-line JSON string -- keys sorted,
    no whitespace after ',' or ':'. `ujson.dumps` does not support the
    `separators` argument at all (passing it raises a TypeError on most
    ujson releases), so we cannot ask ujson to produce the compact form
    directly. Instead we call `ujson.dumps(record, sort_keys=True)` and
    then strip out the incidental whitespace ujson inserts after ',' and
    ':' delimiters, so the final string is byte-identical to what stdlib
    json would have produced with separators=(",", ":"). This keeps the
    audit log format byte-reproducible regardless of which JSON backend
    is installed.

    We deliberately re-validate the result by round-tripping it through
    `ujson.loads` and comparing to the original mapping. If the installed
    ujson version rejects `sort_keys`, or the round-trip check fails
    (e.g. due to NaN/Infinity handling differences), we raise a
    RuntimeError rather than silently emitting a non-deterministic or
    incorrect audit record.
    """
    try:
        raw = ujson.dumps(record, sort_keys=True)
    except TypeError as exc:
        raise RuntimeError(
            "installed ujson version does not support sort_keys; "
            "cannot guarantee deterministic audit log formatting"
        ) from exc

    # Strip whitespace ujson may insert after ',' and ':' to force the
    # exact compact separators stdlib json would use.
    compact = raw.replace(", ", ",").replace(": ", ":")

    # Validate round-trip equivalence; if it doesn't hold, refuse to
    # emit a potentially incorrect/non-deterministic record.
    if ujson.loads(compact) != record:
        raise RuntimeError(
            "ujson-serialized record failed round-trip validation; "
            "refusing to emit non-deterministic audit record"
        )

    return compact


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    _spt_r1 = _axiom_json_dumps_wrapper(record)
    return _spt_r1


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    _spt_r2 = "\n".join(format_reading(**dict(r)) for r in readings)
    return _spt_r2
