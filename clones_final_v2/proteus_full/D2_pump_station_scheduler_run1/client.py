"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper(timeout: float = 2.0):
    """Return an httpx.Client() configured as a drop-in replacement for
    requests.Session().

    `requests.Session()` returns a persistent session object whose
    `.get()`/`.post()` calls return `requests.Response` objects with
    `.json()`, `.raise_for_status()`, and `.status_code` -- and it accepts a
    per-call `timeout=<float>` keyword with no default connect/read timeout
    unless explicitly supplied.

    `httpx.Client()` is the closest analog: it exposes the same
    `.get()`/`.post()`/`.json()`/`.raise_for_status()`/`.status_code`
    duck-typed interface used by `ScadaClient`, so no call-site logic needs
    to change beyond swapping the constructor. However, httpx differs in
    two important ways this wrapper accounts for:

    1. httpx.Client uses an `httpx.Timeout` object (or a plain float, which
       it treats as a uniform timeout) rather than requests' looser
       per-request float; we pass a plain float default here so existing
       per-call `timeout=self.timeout` keyword arguments continue to work
       unchanged.
    2. Unlike requests.Session, httpx.Client holds an underlying connection
       pool that must be explicitly closed (or used via `with` / a context
       manager) -- it does not get cleaned up as forgivingly as
       requests.Session on garbage collection. Callers of this wrapper are
       responsible for closing the returned client when done (e.g. calling
       `.close()` or using it as a context manager) to avoid leaking
       connections.

    Raised HTTP errors also differ: requests raises
    `requests.exceptions.HTTPError` from `.raise_for_status()`, while httpx
    raises `httpx.HTTPStatusError`. Callers catching the old exception type
    will need updating separately; this wrapper does not attempt to mask
    that difference since it only constructs the client.
    """
    import httpx
    _spt_r1 = httpx.Client(timeout=timeout)

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
