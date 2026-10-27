"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import json
import re
from typing import Iterable, Mapping

_SEPARATORS = (",", ":")

# Matches (in priority order): a JSON string literal, a JSON float-looking
# numeric token, or a run of whitespace. Used by the wrapper below to
# normalize ujson's output to look like stdlib json's compact output.
_TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r'|-?\d+\.\d+(?:[eE][+-]?\d+)?'
    r'|-?\d+[eE][+-]?\d+'
    r'|\s+'
)


def _normalize_token(match: "re.Match") -> str:
    """Helper for `_axiom_json_dumps_wrapper`: normalize one regex token.

    String literals are passed through untouched (their internal escaping
    is handled separately). Whitespace runs found *outside* of string
    literals are dropped entirely, which is what reproduces the compact
    `(",", ":")` separator style regardless of whatever spacing the
    backend chose by default. Bare numeric tokens that look like floats
    are reformatted via Python's own `repr(float(...))`, which is exactly
    the routine stdlib `json` uses internally to render floats, so that
    float formatting stays deterministic even if the backend renders
    floats differently (e.g. different precision/trailing zeros).
    """
    token = match.group(0)
    if token.startswith('"'):
        return token
    if token.strip() == "":
        return ""
    try:
        return repr(float(token))
    except ValueError:
        return token


def _ensure_ascii(text: str) -> str:
    """Helper for `_axiom_json_dumps_wrapper`: re-escape non-ASCII chars.

    stdlib `json.dumps` defaults to `ensure_ascii=True`, escaping every
    non-ASCII code point as a `\\uXXXX` sequence (or a surrogate pair for
    code points above the BMP). ujson's non-ASCII escaping behavior has
    historically diverged from this, so this helper walks the already
    -serialized string and re-escapes any remaining raw non-ASCII
    characters the same way stdlib json would, guaranteeing byte-for-byte
    ASCII-only output for the audit log regardless of backend quirks.
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if cp < 128:
            out.append(ch)
        elif cp <= 0xFFFF:
            out.append("\\u{:04x}".format(cp))
        else:
            cp -= 0x10000
            high = 0xD800 + (cp >> 10)
            low = 0xDC00 + (cp & 0x3FF)
            out.append("\\u{:04x}\\u{:04x}".format(high, low))
    return "".join(out)


def _axiom_json_dumps_wrapper(record) -> str:
    """Serialize `record` deterministically, bridging json -> ujson.

    stdlib `json.dumps(record, sort_keys=True, separators=(",", ":"))`
    returns a single-line string with sorted keys and no whitespace
    around the ',' and ':' separators -- the exact compact format this
    module's byte-reproducible audit logs rely on.

    ujson's `dumps()` does not accept a `separators` keyword argument at
    all (passing one raises a TypeError), always emitting its own fixed
    compact/expanded layout instead, and it has historically differed
    from stdlib json in float precision/rounding and in whether
    non-ASCII characters are escaped.

    This wrapper calls the backend's `dumps(record, sort_keys=True)`
    (the module bound to the name `json` in this file may be ujson after
    migration) without the unsupported `separators` kwarg, then
    post-processes the resulting text: whitespace outside of quoted
    string literals is stripped (reproducing the compact `(",", ":")`
    separator style), bare float tokens are re-rendered with Python's
    own `repr(float(...))` (matching stdlib json's float formatting),
    and any remaining non-ASCII characters are re-escaped as `\\uXXXX`
    sequences (matching stdlib json's `ensure_ascii=True` default). The
    combination reproduces stdlib json's deterministic output as closely
    as possible on top of ujson's differing defaults.
    """
    raw = json.dumps(record, sort_keys=True)
    normalized = _TOKEN_RE.sub(_normalize_token, raw)
    return _ensure_ascii(normalized)


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _axiom_json_dumps_wrapper(record)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
