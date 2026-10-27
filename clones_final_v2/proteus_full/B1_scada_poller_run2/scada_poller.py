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
    """Construct a synchronous HTTP client to replace ``requests.Session()``.

    ``requests.Session()`` returns a ``requests.Session`` object whose
    ``.get(url, timeout=...)`` method returns a ``requests.Response`` with
    ``.raise_for_status()`` and ``.json()`` methods, and which verifies TLS
    certificates by default (``verify=True``) with no default timeout unless
    one is passed per-request.

    ``httpx`` has no direct ``Session`` equivalent; the synchronous
    counterpart is ``httpx.Client``. Its constructor takes ``verify`` for TLS
    verification (same default semantics as requests) but should not be
    given a blanket default ``timeout`` here since callers (``ScadaPoller``)
    already pass ``timeout=`` explicitly on each ``.get()`` call, matching
    the per-request timeout behavior of ``requests``.

    This wrapper builds and returns an ``httpx.Client`` (never
    ``httpx.AsyncClient``) so it can be used as a drop-in replacement for
    ``requests.Session()`` in the synchronous ``.get()`` / ``.raise_for_status()``
    / ``.json()`` call chain used by ``ScadaPoller``, without requiring the
    polling code to become async.
    """
    import httpx

    return httpx.Client(verify=True)


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
