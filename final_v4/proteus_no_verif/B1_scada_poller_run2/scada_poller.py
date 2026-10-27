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
    """Return an HTTP client to use in place of ``requests.Session()``.

    ``requests.Session()`` returns a session object whose ``.get(url,
    timeout=...)`` call returns a ``requests.Response`` with
    ``.raise_for_status()`` and ``.json()`` methods, and which never requires
    explicit closing (it is cleaned up by garbage collection).

    ``httpx.Client()`` is the equivalent synchronous client. It exposes the
    same ``.get(url, timeout=...)`` -> response with ``.raise_for_status()``
    and ``.json()`` call chain used by ``ScadaPoller.read_tag``, so no
    transformation of the response is needed. The one real difference is
    resource cleanup: unlike ``requests.Session``, ``httpx.Client`` holds
    onto pooled connections and must be explicitly closed (or used as a
    context manager) or those connections will leak. This wrapper does not
    hide that requirement -- callers (see ``ScadaPoller.close`` /
    ``__enter__``/``__exit__``) are responsible for closing the returned
    client when they are done with it.
    """
    import httpx  # imported lazily so tests can inject a fake session
    _spt_r1 = httpx.Client()

    return _spt_r1


def _default_session():
    _spt_r2 = _axiom_default_session_wrapper()
    return _spt_r2


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        if not (session is not None):
            self.session = _default_session()
        else:
            self.session = session
        self.timeout = timeout

    def read_tag(self, tag: str) -> Mapping:
        resp = self.session.get(f"{self.base_url}/tags/{tag}", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        _spt_r3 = {
            "tag": tag,
            "value": data["value"],
            "quality": data.get("quality", "GOOD"),
        }
        return _spt_r3

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        _spt_r4 = [self.read_tag(t) for t in tags]
        return _spt_r4

    def close(self) -> None:
        """Release the underlying HTTP client's resources.

        Unlike ``requests.Session``, ``httpx.Client`` pools connections that
        must be explicitly released. Callers that construct a ``ScadaPoller``
        without injecting their own session should call ``close()`` (or use
        the poller as a context manager) when finished polling.
        """
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "ScadaPoller":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
