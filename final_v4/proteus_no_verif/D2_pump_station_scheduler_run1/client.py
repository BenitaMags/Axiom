"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

import httpx


def _axiom_scada_client_factory():
    """Construct a client object equivalent to ``requests.Session()``.

    ``requests.Session()`` returns a session object exposing ``.get``/``.post``
    methods that return ``requests.Response`` objects with ``.json()``,
    ``.raise_for_status()`` and ``.status_code``. It accepts a single float
    ``timeout`` value per-request and raises
    ``requests.exceptions.ConnectionError`` on connection failure and
    ``requests.exceptions.HTTPError`` from ``raise_for_status()``.

    ``httpx.Client()`` is the equivalent long-lived, connection-pooling
    object in httpx. It also exposes ``.get``/``.post`` returning
    ``httpx.Response`` objects with matching ``.json()``,
    ``.raise_for_status()`` and ``.status_code`` attributes, so no call-site
    rewriting of those usages is required. httpx accepts a plain float for
    the per-request ``timeout`` argument as shorthand for a uniform
    connect/read/write/pool timeout, so ``ScadaClient``'s
    ``timeout=self.timeout`` float usage keeps working unchanged.

    The one behavioral gap this wrapper does NOT paper over: httpx raises
    ``httpx.ConnectError`` (not ``requests.exceptions.ConnectionError``) on
    connection failure, and ``httpx.HTTPStatusError`` (not
    ``requests.exceptions.HTTPError``) from ``raise_for_status()``. Since the
    existing ``ScadaClient`` methods do not catch either exception type --
    they let the error propagate to their own caller -- there is nothing to
    translate here without inventing behavior that never existed. Re-raising
    or converting these exceptions would only be correct if a specific
    caller depended on the old requests exception type; none does, so this
    wrapper deliberately lets httpx's native exceptions propagate, matching
    the "let it raise" behavior requests had for this class.
    """
    _spt_r1 = httpx.Client()
    return _spt_r1


def _default_session():
    _spt_r2 = _axiom_scada_client_factory()

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
