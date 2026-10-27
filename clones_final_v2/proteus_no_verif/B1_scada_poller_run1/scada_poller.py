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
    """Return an httpx.Client() configured to stand in for requests.Session().

    `requests.Session()` returns a session object whose `.get()` method returns
    a `requests.Response` with `.raise_for_status()` and `.json()` methods, and
    which pools connections across calls for the lifetime of the session.

    `httpx` has no top-level `httpx.Session` -- the equivalent synchronous,
    connection-pooling object is `httpx.Client()`. Its `.get()` method returns
    an `httpx.Response` that also implements `.raise_for_status()` and
    `.json()`, so no further shimming of the call sites below is required.

    This wrapper simply constructs and returns that `httpx.Client()` instance,
    so callers that expect a `requests`-like session (with `.get()`,
    `.raise_for_status()`, `.json()`) keep working unmodified.
    """
    import httpx
    _spt_r1 = httpx.Client()

    return _spt_r1


def _default_session():
    _spt_r2 = _axiom_session_wrapper()
    return _spt_r2


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        if not (session is not None):
            self.session = _default_session()
        else:
            self.session = session
        self.timeout = timeout

    def read_tag(self, tag: str) -> Mapping:
        resp = self.session.get(f"{self.base_url}/tags/{tag}", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        _spt_r3 = {
            "tag": tag,
            "value": data["value"],
            "quality": data.get("quality", "GOOD"),
        }
        return _spt_r3

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        _spt_r4 = [self.read_tag(t) for t in tags]
        return _spt_r4
