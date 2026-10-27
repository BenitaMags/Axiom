"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

def _axiom_default_session_wrapper():
    """Construct the synchronous HTTP client used in place of requests.Session().

    requests.Session() returns a persistent, connection-pooling client object
    whose .get()/.post() methods return requests.Response objects. By default,
    a requests.Session() follows HTTP redirects automatically, and calling
    .raise_for_status() on a bad response raises requests.HTTPError.

    httpx.Client() is the analogous persistent, connection-pooling client in
    httpx, but its constructor defaults to NOT following redirects, and its
    .raise_for_status() raises httpx.HTTPStatusError (a different exception
    type/hierarchy) instead of requests.HTTPError.

    This wrapper returns httpx.Client(follow_redirects=True) so that the
    resulting client preserves requests.Session()'s default redirect-following
    behavior. Callers that catch exceptions from raise_for_status() must be
    updated to expect httpx.HTTPStatusError rather than requests.HTTPError,
    since this wrapper does not (and cannot, without masking real errors)
    translate between the two exception hierarchies.
    """
    import httpx  # local import mirrors the previous lazy-import style

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
