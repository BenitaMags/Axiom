"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

from axiom_http import _default_session, _AxiomRequestException, _AxiomHTTPError


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def _endpoint(self, path: str) -> str:
        return f"{self.base}/{path}"

    def get_demand(self) -> float:
        r = self.session.get(self._endpoint("demand"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()["demand"]

    def get_runtimes(self) -> list:
        r = self.session.get(self._endpoint("runtimes"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        r = self.session.post(
            self._endpoint(f"pump/{pump}"),
            json={"on": on},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return getattr(r, "status_code", 200)
