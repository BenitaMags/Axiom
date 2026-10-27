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
    """Build the HTTP client used by ScadaPoller in place of `requests.Session()`.

    `requests.Session()` returns a `requests.Session` object whose `.get(url,
    timeout=<float>)` call returns a `requests.Response`. Calling
    `.raise_for_status()` on that response raises
    `requests.exceptions.HTTPError` for 4xx/5xx replies, and `.json()` parses
    the body -- both are used directly by `ScadaPoller.read_tag`/`poll`.

    `httpx.Client()` is the nearest httpx equivalent, but it is not a drop-in
    replacement for the constructor: `httpx.Client` accepts an `httpx.Timeout`
    (or a plain float applied uniformly to the connect/read/write/pool
    phases) rather than requests' simple per-request float/tuple timeout, its
    `.get(...)` returns an `httpx.Response`, and `.raise_for_status()` raises
    `httpx.HTTPStatusError` (part of the `httpx.HTTPError` hierarchy) instead
    of `requests.exceptions.HTTPError`.

    This wrapper constructs an `httpx.Client()` so that the existing
    `session.get(...).raise_for_status()` / `.json()` call chain in
    `ScadaPoller.read_tag`/`poll` keeps working unchanged, even though the
    concrete client/response/exception types are now httpx's rather than
    requests'.
    """
    import httpx  # imported lazily, mirroring the original lazy `import requests`

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
