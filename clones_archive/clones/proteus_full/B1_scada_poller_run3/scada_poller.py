"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _scada_default_client_wrapper():
    """Construct an HTTP client object compatible with the ``requests.Session()``
    surface used by ``ScadaPoller``.

    ``requests.Session()`` returns a ``Session`` whose ``.get(url,
    timeout=<float>)`` returns a ``Response`` object; that response's
    ``.raise_for_status()`` raises ``requests.exceptions.HTTPError`` on 4xx/5xx
    status codes, and its ``.json()`` parses the body into Python objects.

    ``httpx.Client()`` returns a ``Client`` object with the same-shaped
    ``.get(url, timeout=...)`` method and a response object that also exposes
    ``.raise_for_status()`` and ``.json()``. The differences that matter here
    are: (1) httpx accepts a plain float timeout directly (as well as richer
    ``httpx.Timeout`` objects), so no conversion is required for the simple
    float timeouts this poller uses, and (2) ``.raise_for_status()`` raises
    ``httpx.HTTPStatusError`` instead of ``requests.exceptions.HTTPError``.

    This wrapper exists so the single point of construction is isolated from
    the polling logic: it builds a plain ``httpx.Client()``, which already
    satisfies the ``.get(url, timeout=<float>)`` / ``.raise_for_status()`` /
    ``.json()`` calling conventions relied on by ``ScadaPoller.read_tag``, so
    no additional shimming of those methods is required beyond constructing
    the right client type in the right place.
    """
    import httpx  # imported lazily so tests can inject a fake client instead

    return httpx.Client()


def _default_session():

    return _scada_default_client_wrapper()


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
