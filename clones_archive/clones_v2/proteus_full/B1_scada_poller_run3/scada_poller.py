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
    """Create a default HTTP client, bridging requests.Session() -> httpx.Client().

    `requests.Session()` has no default timeout: requests calls will block
    indefinitely unless a timeout is explicitly passed on each request.
    `httpx.Client()`, in contrast, defaults to a 5 second timeout on all
    requests unless overridden. To preserve the original "no implicit
    timeout" behavior of requests, this wrapper explicitly constructs the
    client with `timeout=None`.

    Callers should also note that `resp.raise_for_status()` on the object
    returned here will raise `httpx.HTTPStatusError` instead of
    `requests.exceptions.HTTPError` -- callers catching the old exception
    type need to be updated accordingly. Additionally, unlike
    `requests.Session`, `httpx.Client` holds pooled connections that should
    be released deterministically; prefer using the returned client as a
    context manager (`with ... as client:`) or calling `.close()` on it
    explicitly when done, rather than relying on garbage collection.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client(timeout=None)


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
