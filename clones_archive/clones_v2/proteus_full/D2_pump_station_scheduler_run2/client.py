"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `aiohttp`. Unlike
`httpx`, `aiohttp` is async-only, so a synchronous-looking facade is built on
top of it below so that the rest of this module (which is written in a
synchronous style) keeps working. Session is injected.
"""

import asyncio

import aiohttp


class _AiohttpSyncResponse:
    """A synchronous stand-in for a `requests.Response`, backed by data
    already read out of an `aiohttp.ClientResponse`.

    `requests` returns a `Response` object whose `.raise_for_status()` raises
    `requests.exceptions.HTTPError` on 4xx/5xx status codes, and whose
    `.json()` parses the (already fully-buffered) response body synchronously.

    `aiohttp.ClientResponse` instead exposes `.raise_for_status()` and
    `.json()` as coroutines that must be awaited, and the response body is
    only available while the connection/session is still open inside an
    `async with` block.

    This class stores the status code and the already-awaited JSON body
    (captured while the connection was open) so that `.raise_for_status()`
    and `.json()` can be called synchronously afterwards, matching what
    ScadaClient expects from `requests`.
    """

    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None,
                history=(),
                status=self.status_code,
                message=f"HTTP error {self.status_code}",
            )

    def json(self):
        return self._body


class _SyncAiohttpSession:
    """A synchronous facade over `aiohttp.ClientSession`, mimicking the
    subset of `requests.Session`'s interface (`.get`, `.post`) used by
    ScadaClient.

    `requests.Session()` gives a session object whose `.get(url,
    timeout=...)` / `.post(url, json=..., timeout=...)` calls block and
    return a `Response` directly, and can be instantiated/used from anywhere.

    `aiohttp.ClientSession` instead requires all requests to be made with
    `async with` inside a running event loop, and the session itself should
    be created inside that loop.

    Since ScadaClient's methods are plain synchronous methods and changing
    them (and all of their callers) to coroutines is out of scope for this
    substitution, this facade runs a short-lived `aiohttp.ClientSession` and
    request inside `asyncio.run(...)` for every call, fully consuming the
    response body while the connection is open, and returns a
    `_AiohttpSyncResponse` snapshot so the rest of ScadaClient can keep
    calling `.raise_for_status()` / `.json()` synchronously exactly as it did
    with `requests`.
    """

    def get(self, url, timeout=None):
        return asyncio.run(self._request("GET", url, timeout=timeout))

    def post(self, url, json=None, timeout=None):
        return asyncio.run(
            self._request("POST", url, json=json, timeout=timeout)
        )

    async def _request(self, method, url, json=None, timeout=None):
        client_timeout = (
            aiohttp.ClientTimeout(total=timeout) if timeout is not None else None
        )
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(method, url, json=json) as resp:
                try:
                    body = await resp.json()
                except Exception:
                    body = None
                return _AiohttpSyncResponse(resp.status, body)


def _scada_default_session_wrapper():
    """Build an HTTP client object compatible with ScadaClient's usage.

    `requests.Session()` returns a Session whose `.get(url, timeout=...)` and
    `.post(url, json=..., timeout=...)` methods return Response objects with
    `.raise_for_status()` and `.json()`, and raise
    `requests.exceptions.HTTPError` on bad status codes.

    `aiohttp.ClientSession` instead only offers an async API, so this wrapper
    returns a `_SyncAiohttpSession`, which internally drives a real
    `aiohttp.ClientSession` via `asyncio.run(...)` on every call and exposes
    the same synchronous `.get`/`.post` -> object-with-`.raise_for_status()`
    -and-`.json()` shape that ScadaClient (and `requests.Session`) provide,
    so the rest of the module never needs to import `requests` (or call
    `aiohttp` directly) at the call sites.
    """
    return _SyncAiohttpSession()


def _default_session():
    return _scada_default_session_wrapper()


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
