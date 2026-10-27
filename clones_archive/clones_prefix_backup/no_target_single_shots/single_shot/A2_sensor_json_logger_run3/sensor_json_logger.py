# CHOSEN-TARGET: orjson
"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON

Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import orjson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _dumps_sorted_compact(obj) -> str:
    """Serialize obj to a compact JSON string with sorted keys.

    orjson natively produces compact output (no extra whitespace) and
    supports sorting keys via OPT_SORT_KEYS, matching the behaviour of
    json.dumps(obj, sort_keys=True, separators=(",", ":")).
    """
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _dumps_sorted_compact(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
