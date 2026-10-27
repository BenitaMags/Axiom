"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_client_wrapper():
    """Bridge wrapper for the requests.Session() -> httpx.Client() migration.

    `requests.Session()` returns a `requests.Session` object whose `.get()`
    and `.post()` methods return `requests.Response` objects, which expose
    `.raise_for_status()`, `.json()`, and `.status_code` -- all of which are
    used unchanged by `ScadaClient`.

    `httpx.Client()` is the closest httpx equivalent of a `requests.Session`:
    it also supports `.get()`/`.post()` with per-call `timeout=` kwargs, and
    the `httpx.Response` objects it returns likewise implement
    `.raise_for_status()`, `.json()`, and `.status_code`, so no further
    translation of the call sites in `ScadaClient` is required.

    This wrapper simply constructs and returns an `httpx.Client()` instance
    in place of `requests.Session()`, isolating the substitution in one
    place so the rest of the module keeps working unchanged.
    """
    import httpx  # lazy import to mirror the previous lazy `import requests`

    return httpx.Client()


def _default_session():

    return _axiom_default_client_wrapper()


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
