"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Return a drop-in replacement for `requests.Session()` built on httpx.

    `requests.Session()` has no default timeout: requests to it will block
    forever unless a `timeout=` kwarg is passed explicitly at call time (as
    this module always does). `httpx.Client()`, by contrast, defaults to a
    5-second timeout on all four phases (connect/read/write/pool) unless you
    override it. If we constructed a plain `httpx.Client()` here, any code
    path that happened to rely on the "no timeout unless specified" behavior
    of requests would silently start raising `httpx.TimeoutException` instead
    of blocking, which is a behavior change independent of the timeouts this
    module already passes explicitly on each call.

    To keep the substitution a true drop-in, this wrapper constructs the
    `httpx.Client()` with `timeout=None`, which disables httpx's default
    timeout and restores requests' original "wait forever unless told
    otherwise" semantics. The returned client still exposes the `.get()`,
    `.post()`, and response `.json()` / `.raise_for_status()` /
    `.status_code` surface that the rest of this module relies on, since
    httpx's client/response API mirrors requests' for these methods.
    """
    import httpx  # lazy import, mirrors the lazy `import requests` below

    return httpx.Client(timeout=None)


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
