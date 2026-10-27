"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `httpx` (similar but not
identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""


def _scada_httpx_session_wrapper():
    """Build a requests.Session-compatible wrapper around an httpx.Client.

    `requests.Session()` returns a session whose `.get`/`.post` calls return
    a `requests.Response`. Calling `.raise_for_status()` on that response
    raises `requests.exceptions.HTTPError` for 4xx/5xx replies, and
    connectivity problems raise `requests.exceptions.ConnectionError` or
    `requests.exceptions.Timeout`.

    `httpx.Client` is the drop-in transport we now use instead, but it
    returns an `httpx.Response` and raises a *different* exception
    hierarchy: `.raise_for_status()` raises `httpx.HTTPStatusError`, and
    connectivity problems raise `httpx.ConnectError` / `httpx.TimeoutException`.
    The call sites in this module (`get_demand`, `get_runtimes`, `command`)
    were written against the requests-style contract, so calling httpx
    directly would let unexpected exception types escape.

    This wrapper returns a small proxy object exposing `.get`/`.post`
    methods backed by an `httpx.Client`. It intercepts httpx's exceptions
    and translates them into generic, requests-compatible exception types
    (builtin `ConnectionError`/`TimeoutError`, plus a local `HTTPError`
    that carries `.response` like `requests.exceptions.HTTPError` does),
    so the existing error-handling call sites keep working unchanged.
    """
    import httpx

    class HTTPError(Exception):
        """Stands in for requests.exceptions.HTTPError; carries .response."""

        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    class _ResponseProxy:
        def __init__(self, response):
            self._response = response

        def __getattr__(self, item):
            return getattr(self._response, item)

        def raise_for_status(self):
            try:
                self._response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise HTTPError(str(exc), response=self._response) from exc

    class _ClientProxy:
        def __init__(self, client):
            self._client = client

        def _request(self, method, url, **kwargs):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                raise TimeoutError(str(exc)) from exc
            except httpx.ConnectError as exc:
                raise ConnectionError(str(exc)) from exc
            return _ResponseProxy(response)

        def get(self, url, **kwargs):
            return self._request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self._request("POST", url, **kwargs)

    return _ClientProxy(httpx.Client())


def _default_session():

    return _scada_httpx_session_wrapper()


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
