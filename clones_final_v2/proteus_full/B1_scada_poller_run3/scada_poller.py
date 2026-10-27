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
    """Wrapper bridging requests.Session() to httpx.Client().

    `requests.Session()` creates a session object that, by default, follows
    HTTP redirects on requests and manages a connection pool transparently.
    `httpx.Client()`, in contrast, defaults to *not* following redirects
    (`follow_redirects=False`), which would silently change behavior for any
    code relying on the old default.

    This wrapper constructs an `httpx.Client` with `follow_redirects=True` so
    that callers see the same redirect-following behavior they got from
    `requests.Session()`. It returns the httpx.Client instance in place of a
    requests.Session, since both expose compatible `.get()`/`.post()`-style
    methods for the simple polling use in this module.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client(follow_redirects=True)


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
