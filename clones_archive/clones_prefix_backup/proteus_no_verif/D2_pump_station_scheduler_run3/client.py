"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Return an httpx.Client() configured to stand in for requests.Session().

    `requests.Session()` returns a Session object exposing `.get`/`.post`
    methods that accept a per-call `timeout=` kwarg and return Response
    objects with `.raise_for_status()`, `.json()`, and `.status_code`.

    `httpx.Client()` returns a Client object with the same method names and
    a compatible Response API (`.raise_for_status()`, `.json()`,
    `.status_code`), and it also accepts a per-call `timeout=` kwarg on
    `.get`/`.post`, so no extra translation of call arguments is required
    here. This wrapper exists purely to centralize the construction of the
    replacement client object so call sites don't need to reference httpx
    directly, and so any future divergence between the two libraries'
    session/client semantics (e.g. default timeout handling, connection
    pooling behavior) can be patched in one place.
    """
    import httpx  # lazy import for testability, mirrors original lazy import

    return httpx.Client()


def _default_session():

    return _axiom_default_session_wrapper()


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
