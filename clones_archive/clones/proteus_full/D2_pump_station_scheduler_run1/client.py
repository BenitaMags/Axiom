"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _default_httpx_client_wrapper():
    """Construct the default HTTP client used when none is injected.

    `requests.Session()` returns a Session object that follows redirects by
    default and raises `requests.HTTPError` from `raise_for_status()`.

    `httpx.Client()` is the closest equivalent, but by default it does NOT
    follow redirects (unlike requests), and its `raise_for_status()` raises
    `httpx.HTTPStatusError` instead of `requests.HTTPError`.

    This wrapper returns `httpx.Client(follow_redirects=True)` to preserve
    requests' default redirect-following behavior. Callers that catch
    `requests.HTTPError` around `raise_for_status()` must be updated to
    catch `httpx.HTTPStatusError` instead, since that exception type is not
    compatible with the old one.
    """
    import httpx  # lazy import for testability

    return httpx.Client(follow_redirects=True)


def _default_session():

    return requests.Session()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _default_httpx_client_wrapper()
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
