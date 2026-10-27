"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _default_session():
    import httpx  # lazy import for testability

    return requests.Session()


def _axiom_session_wrapper():
    """Bridge wrapper: create an httpx.Client configured like requests.Session().

    `requests.Session()` returns a Session object whose `.get`/`.post` methods
    accept a per-call `timeout` kwarg, raise via `.raise_for_status()`, and
    expose `.json()` and `.status_code` on the returned response object.

    `httpx.Client()` is httpx's equivalent long-lived connection object. Its
    `.get`/`.post` methods also accept a per-call `timeout` kwarg, and its
    response objects likewise expose `.raise_for_status()`, `.json()`, and
    `.status_code`, so no further shimming of the response interface is
    needed here -- we only need to construct the client itself.

    This wrapper isolates that construction so call sites can obtain an
    httpx-backed client without depending on `requests` directly, letting
    the two HTTP libraries' session/client objects be swapped in one place.
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
