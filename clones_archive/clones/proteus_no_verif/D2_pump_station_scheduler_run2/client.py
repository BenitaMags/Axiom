"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

def _axiom_httpx_client_wrapper():
    """Bridge for session/client construction between requests and httpx.

    requests.Session() returns a session object that, by default, follows
    HTTP redirects and exposes .get/.post/.json/.raise_for_status/.status_code
    on its responses. httpx.Client(), however, defaults to NOT following
    redirects (follow_redirects=False), which would silently change behavior
    for any code relying on requests' default redirect-following semantics.

    This wrapper instantiates httpx.Client(follow_redirects=True) so the
    returned object matches requests' default redirect behavior while still
    providing the same .get/.post/.json/.raise_for_status/.status_code
    interface used by the rest of this module.
    """
    import httpx  # lazy import for testability

    return httpx.Client(follow_redirects=True)


def _default_session():

    return _axiom_httpx_client_wrapper()


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
