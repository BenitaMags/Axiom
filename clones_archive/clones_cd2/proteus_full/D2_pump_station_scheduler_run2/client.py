"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _requests_session_to_httpx_client_wrapper():
    """Bridge `requests.Session()` construction to `httpx.Client()`.

    `requests.Session()` returns a `requests.Session` object exposing
    `.get()`, `.post()`, and response objects with `.json()`,
    `.status_code`, and `.raise_for_status()` -- used throughout
    `ScadaClient` for making pooled HTTP requests.

    `httpx.Client()` returns an `httpx.Client` object instead. It happens
    to expose the same surface used here -- `.get()`, `.post()`, and
    response objects with matching `.json()`, `.status_code`, and
    `.raise_for_status()` -- so no field remapping is required; we only
    need to construct the right type of session/client object.

    This wrapper performs that construction swap: it builds an
    `httpx.Client()` and returns it in place of a `requests.Session()`,
    so existing call sites written against the requests session interface
    keep working unchanged after the requests -> httpx migration.
    """
    import httpx  # lazy import, mirrors original lazy-import pattern

    return httpx.Client()


def _default_session():

    return _requests_session_to_httpx_client_wrapper()


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
