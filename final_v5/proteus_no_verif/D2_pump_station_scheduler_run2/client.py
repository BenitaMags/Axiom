"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Construct a synchronous HTTP session-like client.

    `requests.Session()` returns a `requests.Session` object whose `.get()` /
    `.post()` methods return `requests.Response` objects. Those responses
    expose `.raise_for_status()` (raising `requests.HTTPError` on 4xx/5xx),
    `.json()`, and `.status_code`, and the session transparently pools
    connections across calls.

    `httpx.Client()` is the synchronous (not async) equivalent: it also
    pools connections across `.get()`/`.post()` calls and its `httpx.Response`
    objects likewise expose `.raise_for_status()`, `.json()`, and
    `.status_code`. The one behavioral difference is that
    `.raise_for_status()` raises `httpx.HTTPStatusError` instead of
    `requests.HTTPError`. We do not swallow or remap that exception here:
    `ScadaClient` only calls `.raise_for_status()` for its side effect of
    raising on error status, and does not depend on the specific exception
    type, so letting `httpx.HTTPStatusError` propagate unchanged preserves
    the original fail-loudly behavior without inventing a translation layer.

    No timeout is bound here because call sites pass `timeout=` explicitly
    per-request, which both libraries accept as a plain float.
    """
    import httpx  # lazy import for testability, mirrors prior lazy import
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
