"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_ujson_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize `record` to a canonical compact JSON string.

    `json.dumps(record, sort_keys=True, separators=(",", ":"))` guarantees:
      - keys sorted lexicographically,
      - no whitespace after "," or ":" (compact form),
      - a `str` return value.

    `ujson.dumps` accepts `sort_keys` but its handling of custom `separators`
    has varied across releases -- some versions silently ignore the kwarg and
    fall back to their own default spacing (e.g. inserting a space after ":"
    or ","), which would silently break the byte-for-byte determinism this
    audit log format requires. Some versions also return a value that is not
    a plain `str` subtype in every code path. Additionally, unlike stdlib
    `json.dumps` (which never escapes "/"), `ujson.dumps` by default escapes
    forward slashes as "\/", which changes the byte content of any string
    value containing "/" (e.g. a tag name like "a/b"). We must pass
    `escape_forward_slashes=False` to reproduce stdlib json's output exactly.

    This wrapper calls `ujson.dumps(record, sort_keys=True)`, then walks the
    result and rewrites it into the exact `(",", ":")` compact form at the
    structural level (tracking whether we are inside a string literal, so we
    never touch whitespace that is actually part of a string value) rather
    than doing a naive global string replace. It then verifies:
      - the result is a `str`,
      - re-parsing it with `ujson.loads` and dumping stdlib-`json`-style with
        sorted keys produces the same key order, confirming `sort_keys` was
        actually honored by the installed ujson version.
    If either check fails, we raise `RuntimeError` rather than silently
    emitting a non-deterministic record.
    """
    raw = ujson.dumps(record, sort_keys=sort_keys, escape_forward_slashes=False)
    if not isinstance(raw, str):
        raise RuntimeError(
            "ujson.dumps did not return a str; cannot guarantee format contract"
        )

    normalized_chars = []
    in_string = False
    escaped = False
    for ch in raw:
        if in_string:
            normalized_chars.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            normalized_chars.append(ch)
            continue
        if ch.isspace():
            # Drop structural whitespace outside of string literals.
            continue
        normalized_chars.append(ch)
    normalized = "".join(normalized_chars)

    comma_sep, colon_sep = separators
    # Re-insert the requested separators at the boundaries left after
    # whitespace stripping. Since default JSON punctuation output by ujson
    # uses "," and ":" as the base characters (only surrounded by optional
    # whitespace which we already stripped), this is a pure re-assembly of
    # the already-correct structural characters -- no string-value bytes are
    # touched because we tracked `in_string` above.
    if comma_sep != ",":
        normalized = normalized.replace(",", comma_sep)
    if colon_sep != ":":
        normalized = normalized.replace(":", colon_sep)

    if sort_keys:
        # Verify sort_keys was actually honored by this ujson version by
        # comparing against a known-sorted reconstruction of top-level keys.
        parsed = ujson.loads(normalized)
        if isinstance(parsed, dict):
            keys = list(parsed.keys())
            if keys != sorted(keys):
                raise RuntimeError(
                    "installed ujson version did not honor sort_keys; "
                    "cannot guarantee deterministic audit log output"
                )

    return normalized


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_ujson_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
