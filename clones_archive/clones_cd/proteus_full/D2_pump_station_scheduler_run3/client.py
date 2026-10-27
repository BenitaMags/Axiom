"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _requests_session_wrapper():
    """Bridge requests.Session() to httpx.Client().

    `requests.Session()` returns a session object whose `.get()`/`.post()`
    methods accept `timeout=`/`json=` kwargs and return a `requests.Response`
    with `.raise_for_status()`, `.json()`, and `.status_code`. On a 4xx/5xx
    response, `.raise_for_status()` raises `requests.HTTPError`.

    `httpx.Client()` is httpx's equivalent long-lived session object. Its
    `.get()`/`.post()` methods accept the same `timeout=`/`json=` kwargs, and
    the returned `httpx.Response` also exposes `.raise_for_status()`,
    `.json()`, and `.status_code` -- but `.raise_for_status()` raises
    `httpx.HTTPStatusError` instead of `requests.HTTPError`.

    Since `ScadaClient` in this module only ever calls
    `.get()`/`.post()`/`.raise_for_status()`/`.json()`/`.status_code` on the
    session/response objects (never anything requests-specific like
    `requests.HTTPError` directly), a plain `httpx.Client()` instance already
    satisfies that surface without any further translation. This wrapper
    exists so the construction site is isolated and documented, and so any
    future need to normalize exception types (e.g. re-raising
    `httpx.HTTPStatusError` in a requests-compatible way) has a single place
    to do it.
    """
    import httpx  # local import mirrors the lazy import style used below

    return httpx.Client()


def _default_session():

    return _requests_session_wrapper()


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
