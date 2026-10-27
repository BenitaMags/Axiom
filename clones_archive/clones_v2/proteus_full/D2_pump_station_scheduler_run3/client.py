"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_session_factory():
    """Create a synchronous HTTP client session.

    requests.Session() returns a persistent connection-pooling session object
    with attributes like .get/.post that follow redirects by default and use
    a single float `timeout` per request.

    httpx.Client() is the sync-client equivalent, but its constructor
    defaults differ from requests.Session(): most notably it does NOT follow
    redirects by default (requests does), and its connection pool sizing
    defaults differ. To keep behavior equivalent to the previous
    requests.Session()-based code (which followed redirects transparently),
    we explicitly pass follow_redirects=True here. No other requests-specific
    behavior (e.g. exception types) is emulated by this wrapper; callers must
    account for httpx's distinct exception hierarchy separately.
    """
    import httpx

    return httpx.Client(follow_redirects=True)


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
