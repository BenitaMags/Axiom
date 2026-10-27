"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _ujson_dumps_wrapper(record) -> str:
    """Serialize `record` to a canonical, compact JSON string.

    stdlib `json.dumps(record, sort_keys=True, separators=_SEPARATORS)` returns
    a `str` with keys sorted and with the ",", ":" separators (no extraneous
    whitespace around items/keys), which is what makes audit records
    byte-reproducible.

    `ujson.dumps` also returns a `str`, and it supports `sort_keys=True`, but
    it does NOT accept a `separators` kwarg at all -- passing one raises a
    TypeError. Fortunately ujson's default output is already comma/colon
    compact (no spaces after "," or ":"), so dropping `separators` does not
    change the on-the-wire formatting.

    To guard against any subtle whitespace differences from ujson (or future
    version changes) breaking byte-reproducibility, this wrapper strips any
    accidental whitespace immediately following the compact separators before
    returning, so the canonical formatting contract is preserved exactly.
    """
    text = ujson.dumps(record, sort_keys=True)
    # Defensively normalize away any whitespace that might follow a separator,
    # to guarantee the same compact form stdlib json produced with
    # separators=(",", ":").
    text = text.replace(", ", ",").replace(": ", ":")
    return text


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _ujson_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
