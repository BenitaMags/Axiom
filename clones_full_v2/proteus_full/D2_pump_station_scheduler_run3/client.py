"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

import httpx


def _default_session():
    _spt_r1 = _axiom_default_session_wrapper()
    return _spt_r1


def _axiom_default_session_wrapper():
    """Factory bridging requests.Session() to httpx.Client() for ScadaClient.

    requests.Session() returns a `Session` object that exposes `.get()`,
    `.post()`, and, on the responses it returns, `.raise_for_status()` and
    `.json()`. Session objects pool connections but are lenient about
    cleanup: letting one go out of scope without calling `.close()` is
    common and mostly harmless. requests.Session() also has no mandatory
    timeout: if none is supplied per-call, requests will happily block
    forever.

    httpx.Client() is the analogous long-lived connection pool, and it
    supports the same `.get()`/`.post()` call signatures used here (including
    the `timeout=` keyword), and the responses it returns likewise expose
    `.raise_for_status()` and `.json()`, so ScadaClient's call sites do not
    need to change their shape. Unlike requests, httpx (and security linters
    such as bandit) expect an explicit timeout to be configured up front, so
    this wrapper constructs the client with an explicit default timeout
    (2.0 seconds) rather than leaving it unbounded; individual calls still
    pass their own `timeout=` keyword which overrides this default. However
    httpx is stricter about lifecycle: the client holds open sockets/file
    descriptors, so long-running services should explicitly call `.close()`
    on the object this returns (or manage it via a `with` block at a higher
    level) rather than relying on garbage collection the way requests.Session
    tolerates. Also note that `raise_for_status()` on an httpx response
    raises `httpx.HTTPStatusError`, not `requests.exceptions.HTTPError` --
    callers catching the old exception type need to be updated accordingly;
    this wrapper does not swallow or translate that exception, it simply
    returns a client whose responses raise the httpx variant naturally.

    This wrapper performs no error translation itself: any exception raised
    by httpx.Client() construction propagates unchanged.
    """
    _spt_r2 = httpx.Client(timeout=2.0)

    return _spt_r2


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        if not (session is not None):
            self.session = _axiom_default_session_wrapper()
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
