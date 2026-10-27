"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _httpx_session_wrapper():
    """Create an httpx-based stand-in for ``requests.Session()``.

    ``requests.Session()`` returns a ``requests.Session`` object whose
    ``.get(url, timeout=...)`` method returns a ``requests.Response`` with
    ``.raise_for_status()`` and ``.json()`` methods. ``httpx`` has no
    ``Session`` class -- the equivalent long-lived, connection-pooling
    client is ``httpx.Client()``. Fortunately ``httpx.Client`` exposes a
    ``.get(url, timeout=...)`` method that returns an ``httpx.Response``
    with the same ``.raise_for_status()`` / ``.json()`` surface used by
    ``ScadaPoller``, so no further request/response translation is needed
    here -- this wrapper exists purely to bridge the naming/construction
    difference (``Session()`` vs ``Client()``) so callers can keep using
    the same call signature regardless of which library backs them.
    """
    import httpx

    return httpx.Client()


def _default_session():

    return _httpx_session_wrapper()


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
