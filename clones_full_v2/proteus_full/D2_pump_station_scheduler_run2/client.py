"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_session_wrapper(default_timeout: float = 2.0):
    """Construct an httpx.Client configured to behave like requests.Session().

    requests.Session() returns a persistent session object whose .get()/.post()
    methods follow redirects by default and have no mandatory timeout (a
    per-call timeout kwarg is optional and requests itself has no built-in
    default timeout enforcement at the session level). Its responses raise
    requests.HTTPError from .raise_for_status() on 4xx/5xx status codes.

    httpx.Client(), by contrast, does NOT follow redirects by default (it
    requires follow_redirects=True), and its .raise_for_status() raises
    httpx.HTTPStatusError instead of requests.HTTPError. Additionally,
    constructing an httpx.Client() with no timeout at all is flagged by
    static analysis (bandit B113) as a potential hang risk, since a client
    with no timeout configured anywhere could block forever on a stalled
    connection.

    This wrapper returns an httpx.Client with follow_redirects=True and an
    explicit concrete `timeout=default_timeout` set at construction time, so
    the client always has a well-defined timeout even if a call site omits
    one. Call sites in ScadaClient still pass timeout=self.timeout explicitly
    on each .get()/.post() call, which overrides this constructor-level
    default per-request, exactly mirroring the flexibility requests.Session
    offered. This wrapper does NOT swallow or translate exceptions -- callers
    still see whatever httpx raises (e.g. httpx.HTTPStatusError from
    raise_for_status()), matching the original behavior of letting
    requests.HTTPError propagate to callers.
    """
    import httpx  # lazy import for testability
    _spt_r1 = httpx.Client(follow_redirects=True, timeout=default_timeout)

    return _spt_r1



def _default_session():
    _spt_r2 = _axiom_session_wrapper()
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
