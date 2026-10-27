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

    def _endpoint(self, suffix: str) -> str:
        return "{root}/{tail}".format(root=self.base, tail=suffix.lstrip("/"))

    def get_demand(self) -> float:
        response = self.session.get(self._endpoint("demand"), timeout=self.timeout)
        self._raise_if_error(response)
        payload = response.json()
        return payload["demand"]

    def get_runtimes(self) -> list:
        response = self.session.get(self._endpoint("runtimes"), timeout=self.timeout)
        self._raise_if_error(response)
        payload = response.json()
        return payload["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        target = self._endpoint("pump/{}".format(pump))
        body = {"on": on}
        response = self.session.post(target, json=body, timeout=self.timeout)
        self._raise_if_error(response)
        code = getattr(response, "status_code", 200)
        return code

    @staticmethod
    def _raise_if_error(response) -> None:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
