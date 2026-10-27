"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Bridge for the requests.Session() -> httpx.Client() incompatibility.

    `requests.Session()` returns a long-lived Session object whose `.get`/
    `.post` calls return `requests.Response` objects. Those responses expose
    `.json()` (synchronous), `.status_code`, and `.raise_for_status()` which
    raises `requests.HTTPError` on 4xx/5xx responses.

    `httpx.Client()` is the analogous long-lived client object. Its `.get`/
    `.post` calls return `httpx.Response` objects that also expose a
    synchronous `.json()`, `.status_code`, and `.raise_for_status()` -- but
    `raise_for_status()` raises `httpx.HTTPStatusError` instead of
    `requests.HTTPError`. The method names and call signatures
    (`get(url, timeout=...)`, `post(url, json=..., timeout=...)`) are
    otherwise compatible, so no argument translation is required.

    This wrapper constructs and returns an `httpx.Client()` in place of
    `requests.Session()`, preserving the `.get`/`.post`/`.json()`/
    `.raise_for_status()`/`.status_code` interface that `ScadaClient` relies
    on. `ScadaClient` never catches `requests.HTTPError` specifically (it
    only calls `raise_for_status()` and lets exceptions propagate), so no
    exception re-raising/normalization is performed here; if a future caller
    needs to catch the requests-specific exception type, wrap the call sites
    to translate `httpx.HTTPStatusError` -> `requests.HTTPError` there.
    """
    import httpx  # target package for the adapted session object

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
