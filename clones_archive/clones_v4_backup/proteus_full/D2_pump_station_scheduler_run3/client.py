"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _default_session():
    import httpx  # lazy import for testability

    return requests.Session()


def _axiom_session_wrapper():
    """Bridge adapter: build an httpx.Client() usable wherever a requests.Session
    was previously expected.

    requests.Session() returns a persistent session object exposing .get(url,
    timeout=<float>), .post(url, json=<obj>, timeout=<float>), and response
    objects whose .raise_for_status() raises requests.exceptions.HTTPError on
    4xx/5xx responses.

    httpx has no direct "Session" type; the synchronous equivalent is
    httpx.Client(), which offers the same .get()/.post() call signature
    (including json= and timeout= keyword arguments) and the same
    .raise_for_status() method name on responses -- but on failure it raises
    httpx.HTTPStatusError (a subclass of httpx.HTTPError) instead of
    requests.exceptions.HTTPError, and its timeout argument accepts either a
    single float (applied uniformly to connect/read/write/pool phases) or an
    httpx.Timeout object for per-phase control.

    For the subset of the API used by this module (get/post with json= and
    timeout=, plus raise_for_status()), a bare httpx.Client() is a drop-in
    replacement, so this wrapper just constructs and returns one. Callers
    that need to catch HTTP error exceptions must catch httpx's exception
    types rather than requests' when this wrapper is used.
    """

    return httpx.Client()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _axiom_session_wrapper()
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
