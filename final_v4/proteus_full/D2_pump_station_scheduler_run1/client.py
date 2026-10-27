"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Construct a synchronous HTTP client to replace requests.Session().

    requests.Session() returns an object with no default timeout: calls
    made through it block forever unless the caller supplies a per-call
    ``timeout=`` kwarg, which is exactly how this module's call sites use
    it (``session.get(url, timeout=self.timeout)``).

    httpx.Client(), in contrast, applies its own default Timeout(5.0) to
    any call that does not explicitly pass ``timeout=``. Every call site
    here always supplies its own ``timeout=self.timeout`` explicitly, so
    that default would not normally matter -- but constructing the client
    with ``timeout=None`` disables httpx's connect/read/write/pool timeouts
    entirely, which Bandit flags (B113) as an unbounded-timeout HTTP call,
    since a hang at the transport level (e.g. TCP connect stalls) would
    then never time out even before any per-call kwarg is consulted.

    To avoid that, we give the client a concrete, finite default timeout
    (30 seconds) instead of None. This only matters as a fallback: every
    .get()/.post() call in this module passes its own ``timeout=self.timeout``
    explicitly, which overrides the client-level default exactly as
    per-call ``timeout=`` overrides requests.Session() behavior. So normal
    behavior (per-call timeout controls the wait) is unchanged; only the
    hypothetical case of a call site omitting timeout=... becomes bounded
    instead of infinite.

    httpx.Client exposes the same .get()/.post()/.raise_for_status() shape
    used by the call sites in this module, so no other call-site changes
    are required beyond obtaining the client through this wrapper.
    """
    import httpx
    _spt_r1 = httpx.Client(timeout=30.0)

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
