"""json_text_normalizer -- rewrite loosely-formatted JSON text into a
canonical, byte-reproducible form matching stdlib ``json.dumps`` output.

Extracted as its own module because the token-scanning logic here is a
self-contained text-processing concern, independent of how the JSON was
originally produced (ujson, stdlib json, or otherwise).
"""

_WHITESPACE = " \t\n\r"
_NUMBER_START = "-0123456789"
_NUMBER_BODY = "-+.0123456789eE"
_FLOAT_MARKERS = (".", "e", "E")


class _StringSpanTracker:
    """Tracks whether the scanner is currently inside a JSON string
    literal, honoring backslash escapes."""

    def __init__(self):
        self.in_string = False
        self._escape = False

    def consume(self, ch: str) -> bool:
        """Feed one character while inside a string. Returns True if the
        string span continues to include this character as body/close."""
        if self._escape:
            self._escape = False
            return True
        if ch == "\\":
            self._escape = True
            return True
        if ch == '"':
            self.in_string = False
            return True
        return True


def _render_number_token(token: str) -> str:
    """Return the canonical text for a scanned numeric token."""
    if any(marker in token for marker in _FLOAT_MARKERS):
        return repr(float(token))
    return token


def _skip_whitespace(text: str, pos: int, n: int) -> int:
    while pos < n and text[pos] in _WHITESPACE:
        pos += 1
    return pos


def normalize_json_text(text: str, separators) -> str:
    """Rewrite ``text`` (a compact-ish JSON document with no embedded
    newlines) so structural separators exactly match ``separators`` and all
    whitespace outside of string literals is removed, and so every float
    literal is reformatted via ``repr(float(...))`` to match stdlib json's
    deterministic float formatting.
    """
    comma_sep, colon_sep = separators
    out = []
    pos = 0
    n = len(text)
    tracker = _StringSpanTracker()

    while pos < n:
        ch = text[pos]

        if tracker.in_string:
            out.append(ch)
            tracker.consume(ch)
            pos += 1
            continue

        if ch == '"':
            tracker.in_string = True
            out.append(ch)
            pos += 1
            continue

        if ch == ",":
            out.append(comma_sep)
            pos = _skip_whitespace(text, pos + 1, n)
            continue

        if ch == ":":
            out.append(colon_sep)
            pos = _skip_whitespace(text, pos + 1, n)
            continue

        if ch in _WHITESPACE:
            pos += 1
            continue

        if ch in _NUMBER_START:
            start = pos
            pos += 1
            while pos < n and text[pos] in _NUMBER_BODY:
                pos += 1
            out.append(_render_number_token(text[start:pos]))
            continue

        out.append(ch)
        pos += 1

    return "".join(out)
