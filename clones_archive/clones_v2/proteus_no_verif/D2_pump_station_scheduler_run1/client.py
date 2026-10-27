"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Construct a synchronous HTTP client to replace requests.Session().

    `requests.Session()` returns a `requests.Session` object whose `.get()`
    and `.post()` methods raise `requests.exceptions.HTTPError` (via
    `raise_for_status()`) or `requests.exceptions.RequestError` subclasses on
    network failures, and which manages its own connection pool/retries.

    `httpx.Client()` is the synchronous (non-async) equivalent: it exposes
    the same `.get()`/`.post()`/`.raise_for_status()` call surface used by
    `ScadaClient`, but on failure it raises `httpx.HTTPStatusError` (from
    `raise_for_status()`) or `httpx.RequestError` subclasses instead. We
    return a plain `httpx.Client()` (explicitly the sync client, not
    `httpx.AsyncClient`) so any code that pattern-matches on exception type
    should be updated to expect the httpx exception hierarchy rather than
    the requests one; connection pooling/retry defaults also differ between
    the two libraries.
    """
    import httpx

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
