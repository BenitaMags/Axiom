"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_default_session_wrapper(timeout: float = 2.0):
    """Construct an httpx.Client configured to behave like requests.Session().

    `requests.Session()` takes no required arguments and yields an object with
    lenient defaults: no timeout unless passed per-request, and it does not
    strictly need to be closed (though it is good practice). Basic usage is
    `session.get(url, timeout=...)` followed by `.raise_for_status()` and
    `.json()`.

    `httpx.Client()` differs in a few important ways:
      * It accepts a `timeout` argument at construction time, using an
        `httpx.Timeout` object rather than a bare float/tuple, and applies it
        by default to all requests made through the client (per-request
        timeouts can still override it).
      * It raises `httpx.HTTPStatusError` from `.raise_for_status()` instead
        of `requests.exceptions.HTTPError`, and `httpx.RequestError` (and
        subclasses) instead of `requests.exceptions.ConnectionError` for
        network-level failures. Callers that catch requests' exception types
        will need to be updated separately.
      * It must eventually be closed (via `.close()` or as a context manager)
        to release pooled connections; unlike `requests.Session`, letting it
        be garbage-collected without closing is not recommended and may emit
        warnings or leak sockets.

    This wrapper maps the single-float timeout convention used elsewhere in
    this module onto an equivalent `httpx.Timeout(timeout)` (applied to
    connect/read/write/pool alike), and returns a client whose `.get()`,
    `.raise_for_status()`, and `.json()` usage matches the calling code's
    expectations for a `requests.Session()`-like object. The caller remains
    responsible for closing the returned client when done.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client(timeout=httpx.Timeout(timeout))


def _default_session():

    return requests.Session()


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _axiom_default_session_wrapper(timeout)
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
