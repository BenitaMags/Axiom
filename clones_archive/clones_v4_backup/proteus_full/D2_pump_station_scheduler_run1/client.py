"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _scada_default_client_wrapper():
    """Create the HTTP client used by ScadaClient when none is injected.

    Historically this returned `requests.Session()`. A `requests.Session`
    exposes `.get(url, timeout=...)` / `.post(url, json=..., timeout=...)`
    methods that return `Response` objects with `.raise_for_status()`,
    `.json()`, and `.status_code`.

    `httpx.Client()` is the closest httpx equivalent: it offers the same
    `.get`/`.post` method signatures (including plain-float `timeout=`
    kwargs, even though httpx's native type is `httpx.Timeout`) and its
    `Response` objects likewise expose `.raise_for_status()`, `.json()`,
    and `.status_code`. Because the two client classes already line up on
    this surface, no attribute shimming is required here -- this wrapper
    exists so call sites depend on one indirection point instead of
    constructing the client type inline, which keeps the
    requests-exception-vs-httpx-exception (`requests.HTTPError` vs
    `httpx.HTTPStatusError`) and timeout-type differences isolated to a
    single, easily-audited location if callers ever need to normalize them.
    """
    import httpx  # lazy import to mirror the previous lazy `import requests`

    return httpx.Client()


def _default_session():

    return _scada_default_client_wrapper()


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
