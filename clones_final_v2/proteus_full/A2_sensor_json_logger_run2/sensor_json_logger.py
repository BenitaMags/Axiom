"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")


def _axiom_json_dumps_wrapper(record) -> str:
    """Serialize `record` to a compact, deterministic JSON string using ujson.

    Background: stdlib `json.dumps(record, sort_keys=True, separators=(",", ":"))`
    produces a canonical, whitespace-free JSON string with keys sorted and no
    spaces after ',' or ':' -- this exact byte layout is what the audit log
    format depends on for reproducibility.

    ujson.dumps does not accept a `separators` keyword argument at all, so we
    cannot ask ujson to produce the compact form directly. Instead we call
    ujson.dumps(record, sort_keys=True) (which uses ujson's default
    formatting, that may include spaces after separators) and then
    post-process the resulting string to strip any whitespace that follows a
    ',' or ':' character, so the final string exactly matches the compact
    (',', ':') separator convention of the original json.dumps call.

    We deliberately do this stripping generically (scanning the produced
    string) rather than trying to pass formatting flags to ujson, since
    ujson's supported keyword arguments and defaults have varied across
    versions. This keeps output byte-reproducible regardless of the
    installed ujson version's default spacing.

    Note: this whitespace-stripping approach is safe here because JSON
    string values themselves cannot contain a bare ',' or ':' followed by
    whitespace without that whitespace being *inside* a quoted string -- to
    avoid corrupting whitespace that legitimately appears inside string
    values, we only strip whitespace that occurs outside of quoted strings.
    """
    raw = ujson.dumps(record, sort_keys=True, ensure_ascii=False)

    out = []
    in_string = False
    escape = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        out.append(ch)
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in (",", ":"):
                j = i + 1
                while j < n and raw[j] in (" ", "\t", "\n", "\r"):
                    j += 1
                i = j
                continue
        i += 1
    return "".join(out)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
