"""Adapter layer translating httpx transport semantics into a requests-shaped
exception contract, used by scada_poller.ScadaPoller.

Kept as a separate module so the httpx-specific glue code (exception
translation, response wrapping, session adaptation) is isolated from the
polling logic itself.
"""


class HTTPError(Exception):
    """Local stand-in for requests.exceptions.HTTPError.

    Raised by _AxiomResponseWrapper.raise_for_status() so callers written
    against the requests API keep seeing a stable, requests-shaped error type
    instead of httpx.HTTPStatusError.
    """


class ConnectionError(Exception):
    """Local stand-in for requests.exceptions.ConnectionError.

    Raised when the underlying httpx client fails to connect (refused,
    reset, DNS failure, malformed response, etc.).
    """


class Timeout(Exception):
    """Local stand-in for requests.exceptions.Timeout.

    Raised when the underlying httpx client exceeds the configured
    connect/read/write/pool timeout.
    """


_TRANSPORT_ERROR_MAP = ()  # populated lazily below, once httpx is importable


def _translate_get_error(exc, httpx_module):
    """Map an httpx transport exception to the matching local exception type."""
    if isinstance(exc, httpx_module.TimeoutException):
        raise Timeout(str(exc)) from exc
    if isinstance(exc, httpx_module.TransportError):
        raise ConnectionError(str(exc)) from exc
    raise


class _AxiomResponseWrapper:
    """Wraps an httpx.Response so its exception contract matches requests.Response.

    requests.Response.raise_for_status() raises requests.exceptions.HTTPError
    on a 4xx/5xx status. httpx.Response.raise_for_status() raises
    httpx.HTTPStatusError instead -- a type callers written against the
    requests API do not catch. This wrapper forwards .json() unchanged and
    translates raise_for_status()'s exception into the local HTTPError so the
    call chain used by ScadaPoller.read_tag (.get()/.raise_for_status()/.json())
    keeps its original error contract.
    """

    def __init__(self, response):
        self._response = response

    def raise_for_status(self):
        import httpx

        try:
            self._response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPError(str(exc)) from exc

    def json(self):
        return self._response.json()


class _AxiomHttpxSessionAdapter:
    """Presents a requests.Session-like .get() on top of a synchronous httpx.Client.

    requests.Session() returns an object whose .get(url, timeout=...) blocks
    and returns a requests.Response exposing .raise_for_status() and .json().
    httpx.Client() (explicitly the synchronous client, never AsyncClient, so
    ScadaPoller.read_tag's blocking call semantics are preserved) also exposes
    .get(), but a failed connection or an exceeded timeout raises httpx's own
    transport exceptions (httpx.TimeoutException, httpx.TransportError) rather
    than requests.exceptions.Timeout / requests.exceptions.ConnectionError, and
    a bad status raises httpx.HTTPStatusError rather than
    requests.exceptions.HTTPError. This adapter forwards .get() to the wrapped
    client, translates those transport failures into the local
    Timeout/ConnectionError, and wraps the returned Response in
    _AxiomResponseWrapper so raise_for_status() keeps raising the local
    HTTPError -- preserving the exact error contract read_tag was written
    against, without swallowing any failure.
    """

    def __init__(self, client):
        self._client = client

    def get(self, url, timeout=None):
        import httpx

        try:
            response = self._client.get(url, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise Timeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ConnectionError(str(exc)) from exc
        return _AxiomResponseWrapper(response)
