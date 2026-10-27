"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _default_session():
    import httpx  # lazy import for testability

    return httpx.Client()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def _full_url(self, path: str) -> str:
        return f"{self.base}{path}"

    def get_demand(self) -> float:
        endpoint = self._full_url("/demand")
        response = self.session.get(endpoint, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload["demand"]

    def get_runtimes(self) -> list:
        endpoint = self._full_url("/runtimes")
        response = self.session.get(endpoint, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        endpoint = self._full_url(f"/pump/{pump}")
        body = {"on": on}
        response = self.session.post(endpoint, json=body, timeout=self.timeout)
        response.raise_for_status()
        status = getattr(response, "status_code", 200)
        return status
