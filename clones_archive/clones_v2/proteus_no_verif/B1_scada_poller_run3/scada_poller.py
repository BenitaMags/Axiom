"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_requests_session_wrapper():
    """Construct an HTTP client equivalent to requests.Session().

    requests.Session() returns a session object that follows HTTP redirects
    by default and exposes .get/.post/etc. methods returning Response objects
    with .raise_for_status() and .json(). httpx.Client(), in contrast,
    defaults to NOT following redirects, which would silently change the
    behavior of code (like read_tag() below) that relies on requests'
    redirect-following default.

    This wrapper returns httpx.Client(follow_redirects=True) so that the
    resulting object behaves like requests.Session() with respect to
    redirects, while still exposing the same .get/.raise_for_status/.json
    chain used by callers.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client(follow_redirects=True)


def _default_session():
    return _axiom_requests_session_wrapper()


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
