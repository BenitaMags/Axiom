"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _scada_session_factory():
    """Create a synchronous HTTP session-like client for ScadaClient.

    `requests.Session()` returns a `requests.Session` object exposing
    `.get`/`.post`/`.json`/`.raise_for_status`/`.status_code` on the objects
    it returns. `httpx` has no direct `Session` equivalent for sync use;
    the analogous object is `httpx.Client()`, which supports the same
    `.get`/`.post` calls and returns `httpx.Response` objects that also
    expose `.json()`, `.raise_for_status()`, and `.status_code`. We
    therefore return an `httpx.Client()` instance in place of
    `requests.Session()` so existing call sites (which rely on those
    attributes) continue to work unchanged.
    """
    import httpx  # lazy import for testability

    return httpx.Client()


def _default_session():
    return _scada_session_factory()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def get_demand(self) -> float:
        r = self.session.get(f"{self.base}/demand", timeout=self.timeout)
        r.raise_for_status()
        return r.json()["demand"]

    def get_runtimes(self) -> list:
        r = self.session.get(f"{self.base}/runtimes", timeout=self.timeout)
        r.raise_for_status()
        return r.json()["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        r = self.session.post(f"{self.base}/pump/{pump}", json={"on": on},
                              timeout=self.timeout)
        r.raise_for_status()
        return getattr(r, "status_code", 200)
