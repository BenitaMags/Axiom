"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Construct a synchronous HTTP client to replace `requests.Session()`.

    `requests.Session()` returns a `requests.Session` object whose `.get`/
    `.post` methods return `requests.Response` objects supporting
    `.json()`, `.raise_for_status()`, and `.status_code`.

    `httpx.Client()` is the closest httpx equivalent: it is a reusable
    client object with the same `.get`/`.post` method signatures, and its
    `httpx.Response` objects also support `.json()`, `.raise_for_status()`,
    and `.status_code`, so call sites here need no further changes.

    Differences to be aware of: `httpx.raise_for_status()` raises
    `httpx.HTTPStatusError` (not `requests.exceptions.HTTPError`), and
    `httpx.Client` instances hold pooled connections that should be closed
    (via `.close()` or context-manager usage) when no longer needed, unlike
    `requests.Session` which is more forgiving if left open.

    This wrapper isolates that construction so the rest of the module can
    keep using the returned object exactly like a `requests.Session`.
    """
    import httpx  # lazy import for testability
    _spt_r1 = httpx.Client()

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
