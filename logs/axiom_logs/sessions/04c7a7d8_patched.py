"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper(default_timeout: float = 2.0):
    """Return an object usable like a `requests.Session()`.

    `requests.Session()` returns a Session whose `.get`/`.post` methods
    return Response objects with `.json()`, `.raise_for_status()`, and
    `.status_code`, and `requests` does not require a timeout to be bound
    at session-construction time -- callers pass `timeout=` per call (or
    omit it entirely, which requests treats as "wait forever").

    httpx.Client(), by contrast, triggers a Bandit B113 finding ("call to
    httpx without timeout") if constructed with no explicit timeout,
    because an unbounded-by-default client is a common source of hangs.
    To eliminate that finding while preserving the old per-call override
    behavior, we bind a concrete `default_timeout` at construction time
    via `httpx.Client(timeout=...)`. httpx.Client still honors a
    `timeout=` keyword argument passed to individual `.get`/`.post` calls,
    overriding this default for that call only -- so `ScadaClient`'s
    existing pattern of passing `timeout=self.timeout` on every call
    continues to work exactly as it did with requests.

    Caveat kept in mind for callers: httpx's `.raise_for_status()` raises
    `httpx.HTTPStatusError` rather than `requests.HTTPError`. We do not
    catch or convert that here -- doing so would swallow information the
    caller may rely on -- so any exception handling further up the stack
    must be prepared to see the httpx exception type instead.
    """
    import httpx

    return httpx.Client(timeout=default_timeout)


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
