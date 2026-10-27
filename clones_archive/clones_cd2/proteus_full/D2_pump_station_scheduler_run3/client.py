"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

def _axiom_session_factory():
    """Create an HTTP client compatible with ScadaClient's expectations.

    `requests.Session()` returns a `requests.Session` object whose
    `get`/`post` methods return `requests.Response` objects exposing
    `.json()`, `.raise_for_status()`, and `.status_code`.

    `httpx.Client()` returns an `httpx.Client` object whose `get`/`post`
    methods return `httpx.Response` objects. Conveniently, `httpx.Response`
    already implements `.json()`, `.raise_for_status()`, and `.status_code`
    with the same semantics as `requests.Response`, so no per-call
    transformation is required here -- this wrapper simply centralizes the
    session/client construction so callers don't need to know which
    underlying library is in use.
    """
    import httpx  # lazy import to mirror the previous lazy `requests` import

    return httpx.Client()


def _default_session():

    return _axiom_session_factory()


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
