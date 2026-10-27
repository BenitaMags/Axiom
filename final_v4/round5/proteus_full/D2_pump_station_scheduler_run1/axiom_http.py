"""axiom_http -- bridge layer translating httpx's call surface and exception
hierarchy into the shape ScadaClient expects from a requests-like session.

Kept as a separate module so the httpx import (and the adapters that depend
on it) live apart from the client's own public surface.
"""

import httpx


class _AxiomRequestException(Exception):
    """Stand-in for requests.exceptions.RequestException.

    requests.Session.get/post raise this (or a subclass, e.g.
    ConnectionError/Timeout) for transport-level failures such as connection
    refused, DNS failure, or a timeout. httpx raises a family of exceptions
    that all derive from httpx.HTTPError/httpx.TransportError for the same
    failure modes but are not related to requests's hierarchy at all. This
    class gives callers of ScadaClient a single, stable exception type to
    catch for those failures instead of leaking a raw httpx exception type
    they have never seen.
    """


class _AxiomHTTPError(_AxiomRequestException):
    """Stand-in for requests.exceptions.HTTPError.

    requests's Response.raise_for_status() raises requests.exceptions.HTTPError
    (a RequestException subclass) when the response has a 4xx/5xx status.
    httpx.Response.raise_for_status() raises httpx.HTTPStatusError instead,
    which is not related to requests's exception classes. This class
    preserves the "raise_for_status() failures are HTTPErrors, and HTTPErrors
    are RequestExceptions" contract that calling code may rely on, while
    still carrying the underlying response for inspection via .response.
    """

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class _AxiomResponseAdapter:
    """Wraps an httpx.Response so it exposes the subset of the
    requests.Response call surface this codebase actually uses:
    .json(), .status_code, and .raise_for_status().

    requests.Response.raise_for_status() raises requests.exceptions.HTTPError;
    httpx.Response.raise_for_status() raises httpx.HTTPStatusError. This
    adapter translates the latter into _AxiomHTTPError so callers keep
    seeing the exception shape requests gave them.
    """

    def __init__(self, response):
        self._response = response

    def json(self):
        return self._response.json()

    @property
    def status_code(self):
        return self._response.status_code

    def raise_for_status(self):

        try:
            self._response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _AxiomHTTPError(str(exc), response=self) from exc


class _AxiomSessionAdapter:
    """Wraps an httpx.Client so it exposes the subset of the
    requests.Session call surface this codebase actually uses: .get()
    and .post(), returning response objects with .json()/.status_code/
    .raise_for_status().

    requests.Session.get/post raise requests.exceptions.RequestException
    subclasses (ConnectionError, Timeout, ...) for transport failures.
    httpx.Client.get/post raise httpx.HTTPError subclasses (ConnectError,
    TimeoutException, ...) for the same failures, from an unrelated
    exception hierarchy. This adapter translates those into
    _AxiomRequestException so calling code's expectations about what it can
    catch keep holding after the migration.
    """

    def __init__(self, client):
        self._client = client

    def get(self, url, **kwargs):
        return self._request(self._client.get, url, **kwargs)

    def post(self, url, **kwargs):
        return self._request(self._client.post, url, **kwargs)

    def _request(self, method, url, **kwargs):

        try:
            response = method(url, **kwargs)
        except httpx.HTTPError as exc:
            raise _AxiomRequestException(str(exc)) from exc
        return _AxiomResponseAdapter(response)

    def close(self):
        self._client.close()


def _axiom_session_wrapper():
    """Bridge requests.Session() to httpx.Client().

    requests.Session() returns a session object whose .get/.post methods
    return requests.Response objects with .json()/.status_code/
    .raise_for_status() (raising requests.exceptions.HTTPError), and whose
    .get/.post themselves raise requests.exceptions.RequestException
    subclasses on transport failure.

    httpx.Client() is the sync equivalent connection-pooling client, but its
    .get/.post raise httpx's own exception hierarchy (httpx.HTTPError and
    subclasses) both for transport failures and, via .raise_for_status(),
    for 4xx/5xx responses (httpx.HTTPStatusError). Those exceptions are
    unrelated to requests's classes, so code written against requests's
    exception contract would silently stop catching failures if handed a
    bare httpx.Client.

    This wrapper returns a _AxiomSessionAdapter around a fresh httpx.Client()
    that re-exposes get/post/json/status_code/raise_for_status and
    translates httpx's exceptions back into the requests-shaped
    _AxiomRequestException/_AxiomHTTPError so ScadaClient (and anything built
    on top of it) keeps working the same way it did against requests.
    """

    return _AxiomSessionAdapter(httpx.Client())


def _default_session():

    return _axiom_session_wrapper()
