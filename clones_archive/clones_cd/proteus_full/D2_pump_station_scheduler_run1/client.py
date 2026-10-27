"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _default_session():
    import httpx  # lazy import for testability

    return httpx.Session()


def _default_session_wrapper():
    """Return an httpx.Client() configured to stand in for requests.Session().

    `requests.Session()` returns a persistent session object whose `.get()`
    and `.post()` methods return `requests.Response` objects supporting
    `.raise_for_status()`, `.json()`, and `.status_code`.

    `httpx.Client()` is httpx's equivalent persistent client object. Its
    `.get()`/`.post()` methods return `httpx.Response` objects which also
    support `.raise_for_status()`, `.json()`, and `.status_code`, so no
    further per-call adaptation is required here -- this wrapper exists
    purely to centralize the lazy import and construction of the httpx
    client so call sites do not need to reference httpx directly, keeping
    the substitution isolated to one place.
    """

    return httpx.Client()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _default_session_wrapper()
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
