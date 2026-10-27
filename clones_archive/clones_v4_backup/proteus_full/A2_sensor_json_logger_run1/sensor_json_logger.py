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
    """Serialize `record` to a canonical, byte-deterministic JSON string.

    Background: stdlib `json.dumps` was previously called here as
    `json.dumps(record, sort_keys=True, separators=_SEPARATORS)`, i.e. with
    keys sorted and compact separators (",", ":") -- no space after the
    comma or colon. `ujson.dumps` historically has inconsistent/limited
    support for the `separators` keyword argument (in many versions it does
    not accept it at all, or silently ignores it), so passing
    `separators=_SEPARATORS` through to ujson can raise a TypeError or
    produce non-deterministic differences from the required output.

    However, `ujson.dumps` called with `sort_keys=True` and no `separators`
    argument already emits compact output by default (no space after "," and
    no space after ":"), which matches the (",", ":") separators used by the
    original stdlib call for the plain dict records built in
    `format_reading` (str/int/float/bool values only). This wrapper
    therefore omits `separators` entirely and relies on ujson's default
    compact formatting plus `sort_keys=True` to reproduce the exact same
    byte sequence as the original json.dumps call, which is required for
    audit-log determinism.
    """
    return ujson.dumps(record, sort_keys=True)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
