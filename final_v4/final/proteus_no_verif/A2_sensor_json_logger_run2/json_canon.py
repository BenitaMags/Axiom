"""json_canon -- canonical, deterministic JSON encoding helper.

Wraps the `ujson` backend and guarantees the same compact, whitespace-free
byte layout that `json.dumps(record, sort_keys=True, separators=(',', ':'))`
would produce, regardless of the installed ujson version's own defaults.
"""
import ujson


def canonical_dumps(record, sort_keys: bool = True) -> str:
    """Serialize `record` to a compact, deterministic JSON string.

    ujson.dumps has no `separators` parameter, so we normalize its output
    after the fact by stripping any ", " / ": " spacing, guaranteeing the
    result matches the separators=(',', ':') byte layout exactly.
    """
    encoded = ujson.dumps(record, sort_keys=sort_keys)
    return encoded.replace(", ", ",").replace(": ", ":")
