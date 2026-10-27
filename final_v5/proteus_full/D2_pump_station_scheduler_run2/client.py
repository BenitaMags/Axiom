"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _axiom_default_session_wrapper():
    """Build a client that stands in for ``requests.Session()``.

    ``requests.Session()`` returns a session object that follows redirects
    by default, and whose ``.get``/``.post`` calls accept a per-call
    ``timeout=`` keyword, return a response with ``.raise_for_status()``,
    ``.json()`` and ``.status_code``, and raise
    ``requests.exceptions.HTTPError`` (a subclass of ``requests.exceptions.
    RequestException``) from ``raise_for_status()`` on 4xx/5xx responses.

    ``httpx.Client()`` differs in one behaviorally significant way for this
    code: by default it does NOT follow redirects, whereas requests does.
    Its ``.get``/``.post``/``.raise_for_status``/``.json``/``.status_code``
    surface is otherwise call-compatible with what ``ScadaClient`` uses, so
    no method-shape shimming is required. ``raise_for_status()`` raises
    ``httpx.HTTPStatusError`` (a subclass of ``httpx.HTTPError``) instead of
    the requests exception; we intentionally do NOT catch/translate that
    here since no call site inspects the exception type -- they only rely on
    an exception propagating, which httpx still does.

    This wrapper sets ``follow_redirects=True`` so the returned client
    matches requests' default redirect-following behavior, preserving the
    original semantics of ``ScadaClient`` without changing its call sites'
    error-handling expectations.

    It also sets an explicit, non-None default ``timeout`` on the client
    itself (rather than leaving httpx's implicit default in place). httpx's
    own default timeout is 5 seconds, but leaving it unspecified triggers a
    Bandit B113 "call to httpx without timeout" finding, and relying on an
    implicit library default is fragile if that default ever changes. Per-
    call ``timeout=`` keywords passed by ``ScadaClient`` methods still
    override this client-level default, exactly as they would override a
    per-call ``timeout=`` passed to ``requests.Session.get``/``.post``.
    """
    import httpx  # lazy import to mirror the original lazy `import requests`
    _spt_r1 = httpx.Client(follow_redirects=True, timeout=10.0)

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
