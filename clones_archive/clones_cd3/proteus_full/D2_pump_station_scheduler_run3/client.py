"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Bridge wrapper for the requests.Session() -> httpx.Client() migration.

    `requests.Session()` returns a `requests.Session` object whose `.get`/
    `.post` methods return `requests.Response` objects supporting
    `.raise_for_status()`, `.json()`, and `.status_code`. `httpx` has no
    identically-named session/client constructor call site compatible with
    that usage, but `httpx.Client()` returns an `httpx.Client` object whose
    `.get`/`.post` methods return `httpx.Response` objects that support the
    same `.raise_for_status()`, `.json()`, and `.status_code` API. This
    wrapper simply constructs and returns that equivalent `httpx.Client()`
    object so downstream call sites (`get_demand`, `get_runtimes`,
    `command`) keep working unmodified.
    """
    import httpx  # lazy import to mirror the original lazy-import style

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
