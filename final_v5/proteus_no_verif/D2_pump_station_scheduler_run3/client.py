"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Construct the default HTTP client object used by ScadaClient.

    requests.Session() returns a session that, by default, follows HTTP
    redirects transparently and raises requests.HTTPError from
    raise_for_status() on 4xx/5xx responses.

    httpx.Client(), when constructed with no arguments, does NOT follow
    redirects by default (unlike requests.Session) and its
    raise_for_status() raises httpx.HTTPStatusError, not
    requests.HTTPError -- a different exception hierarchy that any
    exception-handling caller must be updated to catch.

    To keep get_demand/get_runtimes/command behaving the same as before
    with respect to redirects, this wrapper explicitly builds the client
    with follow_redirects=True. It does NOT translate the exception type,
    since doing so would require catching and re-raising on every call
    and there is no requests.HTTPError-compatible object to reconstruct
    faithfully; callers relying on raise_for_status() must now expect
    httpx.HTTPStatusError.
    """
    import httpx
    _spt_r1 = httpx.Client(follow_redirects=True)

    return _spt_r1


def _default_session():
    _spt_r2 = _axiom_default_session_wrapper()

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
