"""json_compact -- helper for producing compact, deterministic JSON text.

Wraps a JSON backend (ujson) whose `dumps` does not honor a `separators`
keyword the way stdlib `json.dumps` does, and post-processes its output so
that the requested item/key separators are applied exactly, without
disturbing whitespace that happens to live inside string values.
"""
import ujson


class _SeparatorRewriter:
    """Rewrites incidental whitespace around ':' and ',' tokens in raw JSON.

    Operates on already-serialized JSON text, tracking whether the scan
    position is inside a string literal (respecting backslash escapes) so
    that structural separators are the only thing rewritten.
    """

    def __init__(self, item_sep: str, key_sep: str):
        self._item_sep = item_sep
        self._key_sep = key_sep

    def rewrite(self, raw: str) -> str:
        out = []
        in_string = False
        escape = False
        i = 0
        length = len(raw)
        while i < length:
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
                elif ch == ":":
                    out[-1] = self._key_sep
                    i = self._skip_spaces(raw, i + 1, length)
                    continue
                elif ch == ",":
                    out[-1] = self._item_sep
                    i = self._skip_spaces(raw, i + 1, length)
                    continue
            i += 1
        return "".join(out)

    @staticmethod
    def _skip_spaces(raw: str, start: int, length: int) -> int:
        j = start
        while j < length and raw[j] == " ":
            j += 1
        return j


def compact_dumps(record, sort_keys=True, separators=(",", ":")) -> str:
    """Serialize `record` to compact JSON text with exact separators.

    Calls `ujson.dumps(record, sort_keys=sort_keys)` and rewrites any
    incidental separator whitespace ujson may emit so the result matches
    what stdlib `json.dumps(..., separators=separators)` would produce.
    """
    raw = ujson.dumps(record, sort_keys=sort_keys)
    item_sep, key_sep = separators
    return _SeparatorRewriter(item_sep, key_sep).rewrite(raw)
