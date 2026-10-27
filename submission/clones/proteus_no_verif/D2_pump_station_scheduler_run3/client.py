"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Construct the default HTTP session/client object.

    ``requests.Session()`` returns a ``requests.Session`` instance exposing
    ``.get()``/``.post()`` methods, a ``timeout=`` keyword accepting a plain
    float, and a ``raise_for_status()`` method that raises
    ``requests.HTTPError`` on 4xx/5xx responses.

    ``httpx.Client()`` is the analogous construct: it exposes the same
    ``.get()``/``.post()`` method names and also accepts a plain float for
    ``timeout=`` (httpx transparently coerces it into an ``httpx.Timeout``
    instance), so call sites using ``timeout=self.timeout`` keep working
    unchanged. However, its ``raise_for_status()`` raises
    ``httpx.HTTPStatusError`` instead of ``requests.HTTPError``. This wrapper
    isolates that construction so any code that instantiates the default
    session goes through one place; callers that need to catch HTTP errors
    must be updated to catch ``httpx.HTTPStatusError`` (or a generic
    ``Exception``) rather than ``requests.HTTPError``, since the two
    exception hierarchies are unrelated.
    """
    import httpx  # matches the target library's client API

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
