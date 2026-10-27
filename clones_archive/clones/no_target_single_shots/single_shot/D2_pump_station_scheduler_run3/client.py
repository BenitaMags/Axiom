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

    def get_demand(self) -> float:
        resp = self.session.get(f"{self.base}/demand", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["demand"]

    def get_runtimes(self) -> list:
        resp = self.session.get(f"{self.base}/runtimes", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        payload = {"on": on}
        resp = self.session.post(
            f"{self.base}/pump/{pump}", json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        return getattr(resp, "status_code", 200)
