"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_session_wrapper():
    """Bridge for requests.Session() -> httpx.Client().

    `requests.Session()` returns a persistent session object exposing
    `.get(...)`, `.post(...)`, and, on the responses it returns,
    `.raise_for_status()`, `.json()`, and `.status_code`. `httpx` does not
    have a `requests.Session` type; its equivalent persistent-connection
    object is `httpx.Client()`. Conveniently, `httpx.Client` implements the
    same `.get/.post` methods and its responses implement the same
    `.raise_for_status()/.json()/.status_code` surface used by callers in
    this module, so no further shimming of the returned object is required.
    This wrapper exists purely to isolate the substitution: construct and
    return an `httpx.Client()` here so call sites can keep treating the
    result like a `requests.Session()`.
    """
    import httpx  # lazy import to mirror the original lazy `import requests`

    return httpx.Client()


def _default_session():

    return _axiom_session_wrapper()


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
