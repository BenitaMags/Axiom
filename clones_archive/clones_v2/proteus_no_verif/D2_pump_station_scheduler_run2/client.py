"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Bridge wrapper: build an httpx.Client() to replace requests.Session().

    requests.Session() returns a session object whose .get()/.post() methods
    accept a per-call `timeout=` keyword, whose .raise_for_status() raises
    requests.HTTPError on 4xx/5xx responses, and whose responses expose
    .json() for synchronous body parsing plus a .status_code attribute.

    httpx.Client() returns an analogous object: .get()/.post() also accept a
    per-call `timeout=` keyword, responses also expose .json() and
    .status_code, but .raise_for_status() raises httpx.HTTPStatusError
    instead of requests.HTTPError -- a different exception type/hierarchy
    that any calling code catching HTTP errors needs to be aware of.

    This wrapper performs the minimal transformation needed: it simply
    constructs a plain httpx.Client() with no client-level timeout set, so
    the per-call `timeout=` arguments already used by ScadaClient.get_demand,
    get_runtimes, and command continue to work unchanged. Client lifecycle
    (closing the connection pool) is intentionally left to the caller, same
    as the previous requests.Session()-based contract, since ScadaClient does
    not currently close its session explicitly either.
    """
    import httpx  # local import, mirrors previous lazy-import style

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
