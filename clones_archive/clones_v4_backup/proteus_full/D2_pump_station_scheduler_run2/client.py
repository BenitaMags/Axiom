"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Bridge wrapper: requests.Session() vs httpx.Client().

    What requests returns: `requests.Session()` is a persistent connection
    object whose `.get`/`.post`/etc. methods return `requests.Response`
    objects. Sessions follow HTTP redirects automatically for all verbs by
    default, accept a single float `timeout` per request, and
    `.raise_for_status()` raises `requests.exceptions.HTTPError` on 4xx/5xx
    responses.

    What httpx returns instead: httpx has no `Session` class -- the
    equivalent construct is `httpx.Client()`. Its `.get`/`.post` methods
    return `httpx.Response` objects, which are close to drop-in compatible
    (`.status_code`, `.json()`), but `.raise_for_status()` raises
    `httpx.HTTPStatusError` rather than a requests exception, its timeout
    model is more granular (separate connect/read/write/pool timeouts
    instead of one float), and -- depending on version -- it may not
    follow redirects on non-GET/HEAD methods by default the way requests
    does.

    Transformation applied and why: this wrapper builds an `httpx.Client`
    with `follow_redirects=True` so redirect handling matches requests'
    default behavior for every HTTP verb used by `ScadaClient` (including
    the POST in `command()`). The rest of `ScadaClient` only relies on
    `.get`/`.post` with a plain float `timeout=...` kwarg and on
    `.raise_for_status()`/`.json()`/`.status_code`, all of which httpx
    supports with the same call signature, so no further shimming is
    needed here -- just the redirect-following default has to be aligned.
    """
    import httpx  # lazy import, mirrors the lazy import style used for requests

    return httpx.Client(follow_redirects=True)


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
