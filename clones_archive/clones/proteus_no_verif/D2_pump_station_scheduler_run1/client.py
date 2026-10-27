"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Bridge for requests.Session() -> httpx.Client().

    requests.Session() returns a session object that automatically follows
    HTTP redirects by default, and whose responses expose .raise_for_status(),
    .json(), and .status_code (raising requests.HTTPError on bad status).

    httpx.Client() instead does NOT follow redirects unless explicitly told
    to via follow_redirects=True, although its responses expose the same
    .raise_for_status()/.json()/.status_code surface (raising
    httpx.HTTPStatusError on bad status instead of requests.HTTPError).

    This wrapper constructs httpx.Client(follow_redirects=True) so that the
    redirect-following behavior matches requests' default, keeping the
    downstream .get()/.post()/.raise_for_status()/.json()/.status_code call
    sites in this module working unchanged after the migration.
    """
    import httpx  # lazy import for testability, mirrors _default_session style

    return httpx.Client(follow_redirects=True)


def _default_session():

    return requests.Session()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _axiom_default_session_wrapper()
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
