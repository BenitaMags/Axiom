"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_session_wrapper():
    """Return an object usable as a drop-in for `requests.Session()`.

    `requests.Session()` returns a session object whose `.get(url, timeout=...)`
    method returns a `requests.Response` with `.raise_for_status()` and `.json()`
    methods. `httpx` has no `requests`-style `Session` class -- the equivalent
    long-lived, connection-pooling client is `httpx.Client()`. Fortunately
    `httpx.Client().get(url, timeout=...)` returns an `httpx.Response` that also
    exposes `.raise_for_status()` and `.json()`, so no further per-call
    transformation is required here beyond constructing the right object.

    This wrapper isolates that one-line construction difference so callers
    (like `ScadaPoller`) keep working unchanged against either library.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client()


def _default_session():

    return _axiom_session_wrapper()


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
