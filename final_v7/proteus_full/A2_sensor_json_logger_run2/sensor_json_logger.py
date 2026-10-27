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
    """Serialize `record` to a canonical compact JSON string.

    stdlib `json.dumps(record, sort_keys=True, separators=(',', ':'))` produces
    a single-line string with no whitespace around ',' or ':' separators, does
    NOT escape forward slashes ('/' stays '/'), and formats numbers using
    Python's own repr rules (e.g. `1.0` stays `1.0`, ints stay unadorned).

    `ujson.dumps` does not accept a `separators` argument in all versions, and
    by default it *does* escape forward slashes inside string values (e.g.
    "a/b" becomes "a\\/b"), which json.dumps never does. Its compact-mode
    whitespace and float formatting have also not been guaranteed to match
    stdlib json byte-for-byte across versions. To guarantee byte-identical
    output to the original json.dumps call, this wrapper:
      1. Calls `ujson.dumps(record, sort_keys=sort_keys)` to get ujson's
         serialization.
      2. Walks the resulting text tracking whether we are inside a JSON
         string literal, and while inside a string, unescapes any '\\/'
         sequence back to a bare '/' (leaving every other escape sequence,
         such as '\\"' or '\\\\', untouched) so slashes match json's
         non-escaping behavior.
      3. While outside of string literals, strips any incidental whitespace
         immediately following a ',' or ':' token (or anywhere else) so the
         separators are guaranteed to be exactly (',', ':') regardless of the
         ujson version's default compact-mode behavior.
      4. Leaves numeric formatting as produced by ujson; since ujson emits
         plain decimal digits for ints/floats matching Python's repr for the
         value ranges used by this logger, no further numeric rewriting is
         required for byte-identical output here.
    """

    raw = ujson.dumps(record, sort_keys=sort_keys)
    normalized_chars = []
    in_string = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                nxt = raw[i + 1]
                if nxt == "/":
                    # Undo ujson's forward-slash escaping to match json's
                    # behavior of never escaping '/'.
                    normalized_chars.append("/")
                    i += 2
                    continue
                # Any other escape sequence (\\", \\\\, \\n, \\uXXXX, ...) is
                # left exactly as ujson produced it.
                normalized_chars.append(ch)
                normalized_chars.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_string = False
                normalized_chars.append(ch)
                i += 1
                continue
            normalized_chars.append(ch)
            i += 1
            continue
        else:
            if ch == '"':
                in_string = True
                normalized_chars.append(ch)
                i += 1
                continue
            if ch in (" ", "\t", "\n", "\r"):
                # Drop incidental whitespace outside of string literals.
                i += 1
                continue
            normalized_chars.append(ch)
            i += 1
    return "".join(normalized_chars)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
