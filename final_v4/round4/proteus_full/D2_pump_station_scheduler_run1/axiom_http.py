"""axiom_http -- httpx-backed shims that mimic the requests API surface.

Extracted from client.py so the requests-compatibility glue (exception
shape, response wrapping, session defaults) lives in one reusable place.
"""

import httpx


class _AxiomHTTPError(Exception):
    """Stand-in for requests.exceptions.HTTPError.

    requests.Response.raise_for_status() raises requests.exceptions.HTTPError
    (an Exception subclass that carries the offending response as
    `.response`) on 4xx/5xx status codes. httpx.Response.raise_for_status()
    raises httpx.HTTPStatusError instead, a different class with a different
    constructor. This class is a first-party, stdlib-only substitute with the
    same shape (message + `.response`) so translated errors keep the
    attribute callers of the old code relied on.
    """

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class _AxiomResponse:
    """Wraps an httpx.Response so raise_for_status() matches requests.

    requests returns a requests.Response whose raise_for_status() raises
    requests.exceptions.HTTPError. httpx.Client's .get()/.post() return an
    httpx.Response whose raise_for_status() raises httpx.HTTPStatusError, a
    sibling exception with a different constructor and no drop-in
    compatibility. Every other attribute/method (.json(), .status_code, ...)
    behaves the same on both objects, so this wrapper delegates everything
    through __getattr__ and only intercepts raise_for_status() to re-raise
    the httpx status error as the requests-shaped _AxiomHTTPError, without
    swallowing the failure -- the original exception is chained with
    `from exc`.
    """

    def __init__(self, response):
        self._response = response

    def raise_for_status(self):

        try:
            self._response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _AxiomHTTPError(str(exc), response=self._response) from exc

    def __getattr__(self, name):
        return getattr(self._response, name)


class _AxiomSessionWrapper:
    """Wraps httpx.Client to stand in for a requests.Session.

    requests.Session() returns an object whose .get()/.post() return
    requests.Response objects and whose connection/timeout failures surface
    as requests.exceptions.ConnectionError / Timeout. httpx.Client's
    .get()/.post() return httpx.Response and raise httpx.TransportError /
    httpx.TimeoutException on the same failure modes -- a different
    hierarchy that this file's call sites have never had to catch (they only
    call .raise_for_status(), .json(), and read .status_code). This wrapper
    forwards .get()/.post() to the underlying httpx.Client unchanged --
    including whatever transport/timeout exception httpx raises, since
    nothing here should swallow those -- and wraps only the returned
    response in _AxiomResponse so raise_for_status() keeps its
    requests-shaped contract. Everything else is delegated straight through.
    """

    def __init__(self, client):
        self._client = client

    def get(self, url, **kwargs):
        return _AxiomResponse(self._client.get(url, **kwargs))

    def post(self, url, **kwargs):
        return _AxiomResponse(self._client.post(url, **kwargs))

    def __getattr__(self, name):
        return getattr(self._client, name)


def _axiom_default_session_wrapper():
    """Build a requests.Session()-like object on top of httpx.Client.

    requests.Session() gives a persistent connection pool that follows
    redirects by default and applies no forced timeout unless one is passed
    per-call. httpx.Client defaults to NOT following redirects and applies
    its own default Timeout object, so a bare httpx.Client() is not a
    drop-in replacement. This constructs the httpx.Client with
    follow_redirects=True to match requests' default redirect behavior, then
    wraps it in _AxiomSessionWrapper so downstream .get()/.post()/
    .raise_for_status()/.json()/.status_code usage keeps working and status
    errors keep the requests-shaped exception type.
    """

    client = httpx.Client(follow_redirects=True, timeout=10.0)
    return _AxiomSessionWrapper(client)


def _default_session():

    return _axiom_default_session_wrapper()
