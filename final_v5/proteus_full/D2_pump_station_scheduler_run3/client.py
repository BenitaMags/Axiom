

# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: requests → httpx
# The following functions bridge API incompatibilities between
# requests and httpx. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _axiom_httpx_client_with_default_timeout():
    """Construct an httpx.Client with an explicit default timeout.

    requests.Session() returns a Session object whose .get()/.post() calls
    have no timeout unless the caller supplies one, but requests itself
    never triggers a "missing timeout" warning because Bandit's B113 check
    is specifically about *unbounded* calls -- callers here already pass
    timeout=self.timeout explicitly. httpx.Client(), by contrast, is a
    stateful object whose own default (no timeout argument at all) applies
    to every request made through it whenever a call site doesn't override
    it, so constructing it with no timeout at all is what Bandit's B113
    flags for httpx.

    This wrapper returns httpx.Client(timeout=2.0) -- an httpx.Client
    configured with an explicit, literal, finite default timeout (in
    seconds) baked into the client itself -- instead of a bare
    httpx.Client() or a requests.Session(). The literal float (rather than
    a constructed httpx.Timeout object) keeps the "timeout" keyword
    argument's value trivially a literal number, so both static analysis
    and a human reader can see at a glance that no request made through
    this client can hang forever. Per-call timeout= keyword arguments
    (e.g. self.timeout at each call site) still take precedence and
    override this default when supplied, so behavior for existing call
    sites is unchanged; only the previously-unset default is now bounded.

    No exception handling is added here: any error raised while
    constructing or later using the client (e.g. httpx.HTTPStatusError
    from raise_for_status(), httpx.TimeoutException, connection errors)
    propagates to the caller unchanged, exactly as requests.Session()
    exceptions did before.
    """
    import httpx
    _spt_r1 = httpx.Client(timeout=2.0)

    return _spt_r1
# AXIOM CONNECTOR END

"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_factory():
    """Construct the default HTTP client used by ScadaClient.

    requests.Session() returns an object exposing .get(...)/.post(...)
    methods whose responses provide .raise_for_status() (raising
    requests.HTTPError on 4xx/5xx) and .json(). httpx does not have a
    Session class with an identical name, but httpx.Client() provides the
    same call surface: .get(url, timeout=...), .post(url, json=...,
    timeout=...), and responses with .raise_for_status() (raising
    httpx.HTTPStatusError instead of requests.HTTPError) and .json().

    No data transformation is required here -- ScadaClient only relies on
    this shared subset of the API -- so this wrapper's job is simply to
    construct an httpx.Client in place of a requests.Session, without
    catching or altering any exception the underlying calls may raise, so
    callers keep seeing failures exactly as before (just as
    httpx.HTTPStatusError, not requests.HTTPError).
    """
    _spt_r2 = _axiom_httpx_client_with_default_timeout()

    return _spt_r2


def _default_session():
    _spt_r3 = _axiom_default_session_factory()

    return _spt_r3


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
        _spt_r4 = r.json()["demand"]
        return _spt_r4

    def get_runtimes(self) -> list:
        r = self.session.get(f"{self.base}/runtimes", timeout=self.timeout)
        r.raise_for_status()
        _spt_r5 = r.json()["runtimes"]
        return _spt_r5

    def command(self, pump: int, on: bool) -> int:
        r = self.session.post(f"{self.base}/pump/{pump}", json={"on": on},
                              timeout=self.timeout)
        r.raise_for_status()
        _spt_r6 = getattr(r, "status_code", 200)
        return _spt_r6
