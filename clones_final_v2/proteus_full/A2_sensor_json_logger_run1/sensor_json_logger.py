"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record, *, sort_keys=True):
    """Serialize `record` to a compact, canonical JSON string using ujson.

    Background: stdlib `json.dumps` accepts a `separators` tuple (here
    `(",", ":")`) to force a compact, space-free encoding, and a `sort_keys`
    flag to guarantee deterministic key ordering -- both required for
    byte-reproducible audit logs. `ujson.dumps` has neither an arbitrary
    `separators` keyword (its output format is fixed) nor guaranteed
    cross-version `sort_keys` semantics, so this wrapper reproduces the same
    guarantees on top of ujson:

    1. If `sort_keys` is true and `record` is a mapping, we rebuild it as a
       new dict with keys sorted via the standard-library `sorted()` before
       handing it to `ujson.dumps`, rather than trusting ujson's own
       `sort_keys` implementation to be identical across versions.
    2. ujson's default serialization is already compact (no spaces around
       `,`/`:` separators), which matches the canonical
       `separators=(",", ":")` format stdlib json is configured for here, so
       no further separator post-processing is required -- but if a future
       ujson version were to add whitespace, this is the single place to
       strip it back out to keep the on-disk audit format stable.
    """
    if sort_keys and isinstance(record, Mapping):
        record = {key: record[key] for key in sorted(record)}
    return ujson.dumps(record)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
