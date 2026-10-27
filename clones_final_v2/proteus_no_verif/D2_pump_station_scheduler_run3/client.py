"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_requests_session_wrapper():
    """Wrapper bridging requests.Session() to httpx.Client().

    requests.Session() returns a session object that persists cookies/headers
    across requests, follows redirects by default on .get()/.post(), and
    raises requests.exceptions.HTTPError from .raise_for_status().

    httpx.Client() is the closest sync equivalent, but by default it does
    NOT follow redirects (unlike requests), and its .raise_for_status()
    raises httpx.HTTPStatusError instead of requests.exceptions.HTTPError.

    This wrapper constructs httpx.Client(follow_redirects=True) so that the
    redirect-following behavior matches requests.Session(), keeping the
    downstream .get()/.post()/.json()/.status_code call chain compatible.
    Callers must be aware that raise_for_status() will now raise
    httpx.HTTPStatusError rather than requests.exceptions.HTTPError.
    """
    import httpx  # local import mirrors the original lazy-import style
    _spt_r1 = httpx.Client(follow_redirects=True)

    return _spt_r1


def _default_session():
    _spt_r2 = _axiom_requests_session_wrapper()
    return _spt_r2


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        if not (session is not None):
            self.session = _default_session()
        else:
            self.session = session
        self.timeout = timeout

    def get_demand(self) -> float:
        r = self.session.get(f"{self.base}/demand", timeout=self.timeout)
        r.raise_for_status()
        _spt_r3 = r.json()["demand"]
        return _spt_r3

    def get_runtimes(self) -> list:
        r = self.session.get(f"{self.base}/runtimes", timeout=self.timeout)
        r.raise_for_status()
        _spt_r4 = r.json()["runtimes"]
        return _spt_r4

    def command(self, pump: int, on: bool) -> int:
        r = self.session.post(f"{self.base}/pump/{pump}", json={"on": on},
                              timeout=self.timeout)
        r.raise_for_status()
        _spt_r5 = getattr(r, "status_code", 200)
        return _spt_r5
