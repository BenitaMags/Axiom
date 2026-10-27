"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_default_session_wrapper():
    """Bridge the session-construction gap between `requests` and `httpx`.

    `requests.Session()` returns a `requests.Session` object which exposes a
    `.get(url, timeout=...)` method whose response supports `.raise_for_status()`
    and `.json()`. `httpx` has no `Session` class -- the equivalent construct is
    `httpx.Client()`, which exposes the same `.get()` / `.raise_for_status()` /
    `.json()` call chain used by `ScadaPoller.read_tag`.

    This wrapper simply constructs and returns an `httpx.Client()` instance so
    that call sites written against the `requests.Session()` API keep working
    unchanged after the migration, without needing to know about the renamed
    construct.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client()


def _default_session():

    return _axiom_default_session_wrapper()


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def read_tag(self, tag: str) -> Mapping:
        resp = self.session.get(f"{self.base_url}/tags/{tag}", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return {
            "tag": tag,
            "value": data["value"],
            "quality": data.get("quality", "GOOD"),
        }

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        return [self.read_tag(t) for t in tags]
