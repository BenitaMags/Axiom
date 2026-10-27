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
    """Serialize `record` to a canonical, deterministic one-line JSON string.

    stdlib `json.dumps(record, sort_keys=True, separators=_SEPARATORS)` returns a
    compact string with keys sorted and no extra whitespace, using the exact
    separators given (",", ":").

    `ujson.dumps` does not accept a `separators` kwarg at all (passing it raises
    a TypeError), and it already emits compact output (no spaces after "," or
    ":") by default, so the compact-separator behavior is preserved without
    needing to specify it. It does, however, accept `sort_keys`, which is
    required here for deterministic, byte-reproducible audit logs -- but older
    ujson releases have had inconsistent support for that flag, so we defend
    against silent non-determinism by re-sorting the keys ourselves before
    encoding (via a sorted dict) in addition to passing sort_keys=True.

    Any exception raised during encoding is normalized to a TypeError to match
    the exception type stdlib `json.dumps` raises for unserializable objects,
    since ujson may raise a different exception type on encode failure.
    """
    try:
        ordered_record = {k: record[k] for k in sorted(record.keys())}
        return ujson.dumps(ordered_record, sort_keys=True)
    except TypeError:
        raise
    except Exception as exc:
        raise TypeError(str(exc)) from exc


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
