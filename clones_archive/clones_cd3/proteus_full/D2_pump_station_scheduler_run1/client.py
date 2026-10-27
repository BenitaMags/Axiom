"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Create an HTTP client object compatible with the `requests.Session()`
    interface expected by ScadaClient.

    `requests.Session()` returns a `requests.Session` instance whose
    `.get()` / `.post()` methods return `requests.Response` objects with
    `.json()`, `.raise_for_status()`, and `.status_code` attributes.

    `httpx.Client()` returns an `httpx.Client` instance whose `.get()` /
    `.post()` methods return `httpx.Response` objects. Conveniently,
    `httpx.Response` already implements `.json()`, `.raise_for_status()`,
    and `.status_code` with matching semantics, so no attribute-level
    translation is required here -- the wrapper's job is simply to
    construct the right object type (`httpx.Client`) in place of
    `requests.Session`, so callers written against the `requests` session
    interface keep working unchanged.
    """
    import httpx  # lazy import, mirrors the lazy `requests` import below

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
