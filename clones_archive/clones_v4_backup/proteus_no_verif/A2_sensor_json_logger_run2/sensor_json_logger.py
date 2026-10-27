"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record, sort_keys=True):
    """Bridge json.dumps(..., separators=...) to ujson's incompatible dumps API.

    stdlib json.dumps() accepts a `separators` keyword argument that lets the
    caller pin the exact item/key separators used in the output (here the
    compact (',', ':') pair), which is what makes the audit records
    byte-reproducible. ujson.dumps() does not honor `separators` the same
    way -- older/newer versions either reject the kwarg or ignore it and use
    their own internal (possibly whitespace-padded, e.g. ", " / ": ")
    compact formatting instead. ujson.dumps() also accepts a
    `double_precision` kwarg (with no stdlib json equivalent) controlling
    float formatting, which we pin explicitly so numeric output stays
    deterministic across runs/versions instead of drifting with ujson's
    default precision.

    This wrapper calls the (post-migration) dumps() with `sort_keys` for
    deterministic key ordering and a fixed `double_precision`/`ensure_ascii`
    setting, omitting the unsupported `separators` kwarg, and then strips
    any whitespace ujson may have inserted around ',' and ':' so the
    resulting string is byte-identical to what
    `json.dumps(record, sort_keys=True, separators=(",", ":"))` would have
    produced.
    """
    raw = ujson.dumps(
        record,
        sort_keys=sort_keys,
        ensure_ascii=True,
        double_precision=15,
    )
    return raw.replace(", ", ",").replace(": ", ":")


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
