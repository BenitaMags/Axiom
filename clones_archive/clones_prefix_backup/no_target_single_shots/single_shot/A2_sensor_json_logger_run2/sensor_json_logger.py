# CHOSEN-TARGET: rapidjson
"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON

Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import rapidjson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _dumps(record: dict) -> str:
    """Serialize a dict to canonical compact JSON with sorted keys.

    python-rapidjson's `dumps` supports `sort_keys` but always uses compact
    separators (no spaces) by default, matching the (",", ":") behaviour we
    need, so no explicit separators argument is required.
    """
    return rapidjson.dumps(record, sort_keys=True)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _dumps(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
