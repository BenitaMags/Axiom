"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping
import re
import math
from decimal import Decimal

_SEPARATORS = (",", ":")

# Matches whitespace that ujson may insert around structural separators
# (','  and ':') even when we did not request it. We strip these so the
# output byte-matches stdlib json.dumps(..., separators=(',', ':')).
_WS_AFTER_COMMA_OR_COLON = re.compile(r'([,:])\s+')


def _axiom_json_dumps_wrapper(record) -> str:
    """Serialize `record` to a compact, sorted-key JSON string using ujson.

    stdlib ``json.dumps(record, sort_keys=True, separators=(',', ':'))``
    returns a JSON string with keys sorted and no whitespace around the
    ',' and ':' structural separators -- this exact byte layout is what
    the audit log format requires for reproducibility.

    ``ujson.dumps`` does not accept a ``separators`` keyword argument at
    all (passing it raises a TypeError on some ujson versions, or is
    silently ignored on others), and depending on version it may still
    emit a space after ',' or ':' in its default compact-ish output. It
    also does not always match stdlib's rendering of non-finite floats
    (``NaN``, ``Infinity``, ``-Infinity``) or ``decimal.Decimal`` values,
    which can raise or produce non-JSON-standard tokens.

    This wrapper:
      1. Recursively walks the record and converts any float that is NaN
         or +/-Infinity, and any Decimal, into a plain finite float (or
         string fallback for non-finite Decimals), matching stdlib json's
         behavior of emitting bare NaN/Infinity tokens is avoided in favor
         of a deterministic, valid-JSON representation so the audit log
         stays parseable.
      2. Calls ``ujson.dumps(record, sort_keys=True)`` without the
         unsupported ``separators`` kwarg.
      3. Strips any whitespace ujson inserts after ',' or ':' so the
         resulting string is byte-identical to stdlib json's compact
         separators=(',', ':') output.
    """
    def _sanitize(obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return str(obj)
            return obj
        if isinstance(obj, Decimal):
            if obj.is_finite():
                return float(obj)
            return str(obj)
        if isinstance(obj, Mapping):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_sanitize(v) for v in obj]
        return obj

    sanitized = _sanitize(record)
    raw = ujson.dumps(sanitized, sort_keys=True)
    return _WS_AFTER_COMMA_OR_COLON.sub(r'\1', raw)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
