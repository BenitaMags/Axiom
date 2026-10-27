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
    """Construct the HTTP client used by ScadaPoller when no session is injected.

    The original `requests` based helper returned `requests.Session()`, whose
    `.get(url, timeout=...)` accepts a plain float timeout, whose
    `.raise_for_status()` raises `requests.HTTPError`, and whose responses
    expose `.json()` directly. httpx's synchronous client, `httpx.Client()`,
    supports the same call surface (`.get(url, timeout=float)`,
    `.raise_for_status()`, `.json()`) but is a different class with different
    construction semantics (e.g. connection pooling/timeout defaults), and it
    raises `httpx.HTTPStatusError` instead of `requests.HTTPError`. This
    wrapper isolates that construction so call sites can keep using the same
    session-like interface without depending on which package is installed,
    and so we always get the *synchronous* client (httpx also offers an
    async client, which would break this synchronous polling code).
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client()


def _default_session():

    return requests.Session()


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _axiom_default_session_wrapper()
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
