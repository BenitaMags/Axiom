"""client -- SCADA HTTP client for the pump station.

The HTTP client is one substitution target: `requests` -> `aiohttp` (aiohttp is
async-only, so a synchronous adapter is required). Session is injected.
"""

import asyncio

import aiohttp


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: requests → aiohttp
# The following functions/classes bridge API incompatibilities between
# requests and aiohttp. They are injected by the AXIOM connector agent
# and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
class _AxiomSyncResponse:
    """Stand-in for a requests.Response, backed by data already read from
    an aiohttp.ClientResponse.

    requests.Response reads the body eagerly during the blocking call and
    exposes .status_code, .json(), and .raise_for_status() (raising
    requests.exceptions.HTTPError on 4xx/5xx) as ordinary synchronous
    methods that can be used at any point after the call returns.

    aiohttp.ClientResponse instead exposes .status, an async .json(), and
    a .raise_for_status() that both must be invoked while the response
    (and its owning ClientSession) are still open inside a coroutine --
    none of which are available to plain synchronous code once the
    `async with` block used to fetch it has exited.

    _axiom_request() builds this object *while* still inside that async
    context: it captures .status, the parsed body, and the real
    request_info/history off the live aiohttp response, then exposes them
    synchronously here so callers written against requests's Response
    shape keep working without themselves becoming async.
    """

    def __init__(self, status, payload, request_info, history):
        self.status_code = status
        self._payload = payload
        self._request_info = request_info
        self._history = history

    def raise_for_status(self):
        """requests.Response.raise_for_status() raises
        requests.exceptions.HTTPError for 4xx/5xx responses.

        aiohttp's equivalent, ClientResponse.raise_for_status(), raises
        aiohttp.ClientResponseError -- but only works while the underlying
        response/session are still open, which is no longer true by the
        time a caller gets around to calling this method here.

        We reproduce the same trigger condition (status >= 400) and raise
        aiohttp.ClientResponseError ourselves, using the real
        request_info/history captured from the original response so the
        exception is a faithful aiohttp error rather than a fabricated
        one, and so the failure still reaches the caller instead of being
        swallowed.
        """
        if self.status_code >= 400:
            raise aiohttp.ClientResponseError(
                self._request_info,
                self._history,
                status=self.status_code,
                message=f"HTTP {self.status_code}",
            )

    def json(self):
        _spt_r1 = self._payload
        return _spt_r1


def _axiom_request(method, url, timeout=None, json=None):
    """Perform one blocking HTTP call using aiohttp, standing in for calls
    like requests.Session().get(url, timeout=...) / .post(url, json=...).

    requests's Session.get()/.post() block the calling thread and return
    an already-populated Response synchronously. aiohttp's
    ClientSession.get()/.post() instead return an async context manager
    that must be entered/awaited from inside a coroutine, with the body
    read via a further `await resp.json()` before the response closes.

    This wrapper opens a short-lived aiohttp.ClientSession, performs the
    request, awaits the body, and drives the whole coroutine to
    completion with asyncio.run() so the call remains synchronous from
    the caller's point of view -- matching requests's blocking call
    semantics -- returning a _AxiomSyncResponse that exposes the same
    .status_code/.json()/.raise_for_status() surface callers already use.
    Any exception aiohttp raises (e.g. aiohttp.ClientError on connection
    failure, asyncio.TimeoutError on timeout) propagates unchanged out of
    asyncio.run(), exactly as requests's exceptions would have propagated
    out of the original synchronous call -- nothing here is caught and
    discarded.
    """

    async def _do():
        if not (timeout is not None):
            client_timeout = None
        else:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(method, url, json=json) as resp:
                _spt_r2 = _AxiomSyncResponse(
                    resp.status, (await resp.json(content_type=None)), resp.request_info, resp.history
                )
                return _spt_r2
    _spt_r3 = asyncio.run(_do())

    return _spt_r3


class _AxiomSessionFacade:
    """Synchronous facade over aiohttp, standing in for a requests.Session().

    requests.Session() returns a persistent, reusable object whose
    .get()/.post() issue blocking HTTP calls directly. aiohttp has no
    synchronous session equivalent: an aiohttp.ClientSession must be
    created and used inside an event loop via `async with`, so it cannot
    simply be constructed once in __init__ and reused synchronously
    afterwards the way a requests.Session is.

    This facade keeps the .get()/.post() call shape callers already use,
    delegating each call to _axiom_request(), which opens a fresh
    aiohttp.ClientSession per call and runs it to completion synchronously
    via asyncio.run(). This lets ScadaClient's public (synchronous) API
    stay unchanged while the actual network I/O underneath uses aiohttp.
    """

    def get(self, url, timeout=None):
        _spt_r4 = _axiom_request("GET", url, timeout=timeout)
        return _spt_r4

    def post(self, url, json=None, timeout=None):
        _spt_r5 = _axiom_request("POST", url, timeout=timeout, json=json)
        return _spt_r5


def _axiom_default_session_wrapper():
    """Return an object standing in for requests.Session() with .get()/.post().

    See _AxiomSessionFacade's docstring for the detailed rationale:
    aiohttp.ClientSession cannot be constructed once and reused
    synchronously the way requests.Session() is, since it requires an
    active event loop and `async with` usage. This wrapper returns a
    _AxiomSessionFacade instead, whose .get()/.post() reproduce the
    blocking call semantics of requests.Session() on top of aiohttp.
    """
    _spt_r6 = _AxiomSessionFacade()
    return _spt_r6


def _default_session():
    _spt_r7 = _axiom_default_session_wrapper()

    return _spt_r7


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
        _spt_r8 = r.json()["demand"]
        return _spt_r8

    def get_runtimes(self) -> list:
        r = self.session.get(f"{self.base}/runtimes", timeout=self.timeout)
        r.raise_for_status()
        _spt_r9 = r.json()["runtimes"]
        return _spt_r9

    def command(self, pump: int, on: bool) -> int:
        r = self.session.post(f"{self.base}/pump/{pump}", json={"on": on},
                              timeout=self.timeout)
        r.raise_for_status()
        _spt_r10 = getattr(r, "status_code", 200)
        return _spt_r10
