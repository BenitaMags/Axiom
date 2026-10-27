# CHOSEN-TARGET: httpx
"""client -- SCADA HTTP client for the pump station.


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

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base}{path}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    def get_demand(self) -> float:
        r = self._request("GET", "/demand")
        return r.json()["demand"]

    def get_runtimes(self) -> list:
        r = self._request("GET", "/runtimes")
        return r.json()["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        r = self._request("POST", f"/pump/{pump}", json={"on": on})
        return getattr(r, "status_code", 200)
