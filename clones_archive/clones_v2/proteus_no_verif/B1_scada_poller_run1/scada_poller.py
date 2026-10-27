"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _scada_httpx_client_wrapper():
    """Build an HTTP client to replace requests.Session() for ScadaPoller.

    requests.Session() returns a session object that follows HTTP redirects
    by default on .get()/.post()/etc., and exposes .raise_for_status() on
    its response objects which raises requests.exceptions.HTTPError for
    4xx/5xx responses.

    httpx.Client(), in contrast, defaults to follow_redirects=False, and its
    response.raise_for_status() raises httpx.HTTPStatusError instead. To
    keep read_tag()'s behavior identical to the original requests-based
    code (redirects silently followed, errors raised the same way callers
    expect), we construct the httpx.Client with follow_redirects=True
    explicitly. The resulting object still supports the same .get() /
    .raise_for_status() / .json() call pattern used by read_tag(), so no
    other code needs to change.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client(follow_redirects=True)


def _default_session():
    return _scada_httpx_client_wrapper()


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
