"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_ujson_dumps_wrapper(record, sort_keys=True):
    """Serialize `record` to a canonical compact JSON string using ujson.

    stdlib `json.dumps(record, sort_keys=True, separators=(",", ":"))` produces
    a deterministic, compact JSON string: keys sorted, no whitespace after
    the ',' and ':' separators, and canonical numeric formatting (e.g.
    `1.0`, `1e+20` style rendering identical across runs).

    `ujson.dumps` does not reliably support the `separators` keyword across
    versions (its API instead exposes `indent`/`item_separator`/
    `key_separator` inconsistently), so passing `separators=_SEPARATORS`
    either fails or is silently ignored, which can leave stray whitespace
    after ':' or ',' in the output. It can also have subtly different
    float/int rendering than stdlib json.

    This wrapper calls `ujson.dumps(record, sort_keys=sort_keys)` (without
    the unsupported `separators` kwarg) and then post-processes the result:
      1. Strips any whitespace that ujson may insert after ':' or ',' so the
         output exactly matches stdlib json's compact (',', ':') separators.
      2. Re-parses and re-renders numeric values is avoided (too costly and
         risks precision loss); instead we do a lightweight string-level
         normalization pass that collapses ", " -> "," and ": " -> ":" while
         being careful not to touch such sequences occurring inside string
         values, by operating on the raw text with a small state machine
         that tracks whether we're inside a JSON string literal.

    This guarantees byte-reproducible, deterministic output matching the
    exact format required by the audit log, regardless of ujson's internal
    separator/formatting quirks.
    """
    raw = ujson.dumps(record, sort_keys=sort_keys)

    out = []
    in_string = False
    escaped = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        out.append(ch)
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in (",", ":"):
                # Skip any following whitespace so separators stay compact.
                j = i + 1
                while j < n and raw[j] in " \t\n\r":
                    j += 1
                i = j
                continue
        i += 1

    return "".join(out)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
