"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Return a session-like object for making HTTP calls.

    `requests.Session()` returns a `requests.Session` instance exposing
    .get/.post/.raise_for_status()/.json()/.status_code, and it is used as a
    long-lived object holding connection pools/cookies across calls.

    httpx has no top-level "Session" -- its equivalent is `httpx.Client()`,
    a sync client object that exposes the same .get/.post methods and
    returns response objects with .raise_for_status()/.json()/.status_code,
    matching the contract ScadaClient expects. This wrapper simply
    constructs and returns an `httpx.Client()` instance in place of
    `requests.Session()` so callers do not need to change.

    Note: exception types raised on error differ between the two libraries
    (requests.exceptions.* vs httpx.HTTPStatusError/httpx.RequestError);
    any error-handling call sites must account for that separately.
    """
    import httpx  # lazy import for testability

    return httpx.Client()


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
