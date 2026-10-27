"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_session_factory_wrapper(timeout: float = 2.0):
    """Bridge for `requests.Session()` -> `httpx.Client()`.

    `requests.Session()` returns a `requests.Session` object whose `.get()` /
    `.post()` methods return `requests.Response` objects: these expose
    `.json()`, `.status_code`, and a `.raise_for_status()` that raises
    `requests.exceptions.HTTPError` on 4xx/5xx responses. Critically,
    `requests.Session()` carries no default connect/read timeout of its own
    hazard -- each call site here already passes an explicit `timeout=`
    kwarg, so no request is ever unbounded.

    httpx's synchronous equivalent of `requests.Session` is `httpx.Client`
    (NOT `httpx.AsyncClient`, which requires `await` and would break this
    synchronous call pattern). `httpx.Client().get()` / `.post()` return
    `httpx.Response` objects which also expose `.json()`, `.status_code`,
    and a `.raise_for_status()`, but the latter raises
    `httpx.HTTPStatusError` instead of `requests.exceptions.HTTPError`.

    Unlike `requests.Session`, `httpx.Client` built with no `timeout=`
    argument still works per-call (each `.get()`/`.post()` here passes its
    own `timeout=`), but constructing the client bare trips static
    analysis (bandit B113: "Call to httpx without timeout") because the
    client itself carries no explicit bound. This wrapper closes that gap
    by always constructing `httpx.Client(timeout=timeout)` with an explicit
    float (matching `ScadaClient`'s own `timeout` default/parameter), while
    leaving the per-call `timeout=` overrides in `get()`/`.post()` free to
    still take precedence for any individual request.

    No exception translation is performed here because nothing in this
    module catches `requests.exceptions.HTTPError` specifically -- the
    httpx error is simply left to propagate to the caller, exactly as the
    requests error used to.
    """
    import httpx

    return httpx.Client(timeout=timeout)


def _default_session(timeout: float = 2.0):

    return _axiom_session_factory_wrapper(timeout=timeout)


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session if session is not None else _default_session(timeout=self.timeout)

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
