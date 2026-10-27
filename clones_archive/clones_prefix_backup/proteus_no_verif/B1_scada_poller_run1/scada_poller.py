"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _httpx_default_session_wrapper():
    """Construct an httpx client compatible with the previous requests.Session() usage.

    `requests.Session()` returns a session object whose `.get(url, timeout=...)`
    call yields a `requests.Response` with `.raise_for_status()` and `.json()`
    methods, and which can be reused across many requests without explicit
    cleanup. `httpx` instead exposes `httpx.Client()`, which offers the same
    `.get(url, timeout=...)` call and the same `.raise_for_status()`/`.json()`
    response API, so no data transformation is required -- the wrapper simply
    constructs the httpx equivalent object so call sites written against the
    requests.Session() interface keep working unmodified.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client()


def _default_session():

    return _httpx_default_session_wrapper()


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
