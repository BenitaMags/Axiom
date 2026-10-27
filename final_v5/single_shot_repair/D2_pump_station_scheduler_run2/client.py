"""client -- SCADA HTTP client for the pump station.

Uses httpx as the underlying HTTP library (replacing requests). httpx's
Client API differs from requests.Session in construction and lifecycle, so a
thin adapter is provided to preserve the exact session interface
(.get/.post with a timeout kwarg, response objects exposing .json(),
.raise_for_status() and .status_code) that the rest of this module relies on.
"""


class _HttpxSessionAdapter:
    """Adapts an httpx.Client to the subset of the requests.Session API
    this client depends on, so ScadaClient's calling code needn't change."""

    def __init__(self, client):
        self._client = client

    def get(self, url, timeout=None, **kwargs):
        return self._client.get(url, timeout=timeout, **kwargs)

    def post(self, url, json=None, timeout=None, **kwargs):
        return self._client.post(url, json=json, timeout=timeout, **kwargs)

    def close(self):
        self._client.close()


def _default_session():
    import httpx  # lazy import for testability

    return _HttpxSessionAdapter(httpx.Client())


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
