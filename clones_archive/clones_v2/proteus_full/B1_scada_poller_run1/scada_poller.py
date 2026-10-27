"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _scada_httpx_client_factory():
    """Create an HTTP client object usable by ScadaPoller in place of requests.Session().

    requests.Session() returns an object whose .get(...) yields a Response with
    .raise_for_status() (raising requests.exceptions.HTTPError on 4xx/5xx) and
    .json() (parsing the body as JSON), and which has no default timeout unless
    passed explicitly per-request.

    httpx.Client() returns an analogous object: .get(...) yields an httpx.Response
    which also exposes .raise_for_status() (raising httpx.HTTPStatusError instead
    of requests.exceptions.HTTPError) and .json(). However, httpx.Client() applies
    its own default timeout semantics unless one is supplied.

    This wrapper simply constructs an httpx.Client() with an explicit default
    timeout so the resulting object can be used as a drop-in replacement for
    requests.Session() in ScadaPoller's .get()/.raise_for_status()/.json() call
    chain, without requiring any changes to that call chain itself.
    """
    import httpx  # imported lazily so tests can inject a fake client

    return httpx.Client(timeout=5.0)


def _default_session():
    return _scada_httpx_client_factory()


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _scada_httpx_client_factory()
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
