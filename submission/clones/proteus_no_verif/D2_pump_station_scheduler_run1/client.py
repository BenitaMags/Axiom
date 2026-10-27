"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Bridge requests.Session() -> httpx.Client() for the default session.

    requests.Session() returns a requests.Session object which pools
    connections and, importantly, follows HTTP redirects by default for
    all verbs (GET/POST/etc). httpx.Client, on the other hand, defaults
    to follow_redirects=False -- a naive swap would silently change the
    client's behavior on any SCADA endpoint that issues a redirect.

    This wrapper constructs an httpx.Client(follow_redirects=True) so the
    resulting object matches requests.Session's default redirect-following
    behavior, closing that API gap.

    Note: unlike requests.Session, an httpx.Client owns real OS resources
    (its connection pool) and must be closed explicitly (via .close()) or
    used as a context manager. ScadaClient has no __del__ or context
    manager protocol, so the caller that obtains a session from here (and
    ultimately whoever owns the ScadaClient) is responsible for closing
    it, e.g. `client.session.close()`, when done.
    """
    import httpx  # lazy import for testability

    return httpx.Client(follow_redirects=True)


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
