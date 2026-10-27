"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping

import httpx


class AxiomHTTPError(Exception):
    """Stand-in for requests.exceptions.HTTPError.

    requests.Response.raise_for_status() raises this (with a ``.response``
    attribute pointing back at the response that failed) when the status
    code indicates an error. httpx.Response.raise_for_status() raises
    httpx.HTTPStatusError instead, a different class with a different
    constructor signature. Call sites (in this file or elsewhere) that were
    written against the requests error contract must keep seeing this type,
    so the response adapter below catches httpx.HTTPStatusError and
    re-raises this instead of letting the httpx-specific type escape.
    """

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class AxiomConnectionError(Exception):
    """Stand-in for requests.exceptions.ConnectionError.

    requests raises this for DNS failures, refused connections, and other
    transport-level breakage. httpx raises httpx.TransportError (and its
    subclasses, e.g. httpx.ConnectError) for the same failures instead, so
    the session adapter below translates those into this class rather than
    letting them reach callers unconverted.
    """


class AxiomTimeoutError(Exception):
    """Stand-in for requests.exceptions.Timeout.

    requests raises this when a request exceeds its timeout. httpx raises
    httpx.TimeoutException (a sibling of httpx.TransportError, not a
    subclass) for the same condition, so it must be caught and translated
    separately rather than assuming the transport-error handler covers it.
    """


class _AxiomResponseAdapter:
    """Wrap an httpx.Response so it behaves like a requests.Response.

    requests.Response.raise_for_status() raises
    requests.exceptions.HTTPError; httpx.Response.raise_for_status() raises
    httpx.HTTPStatusError. Callers here call raise_for_status() and then
    .json(), so this adapter forwards .json() untouched but translates the
    error raised by raise_for_status() so the failure mode looks the same
    as it did under requests. Any other attribute access (e.g. .text,
    .status_code) is forwarded straight through to the wrapped response.
    """

    def __init__(self, response):
        self._response = response

    def raise_for_status(self):

        try:
            self._response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AxiomHTTPError(str(exc), response=self) from exc

    def json(self):
        return self._response.json()

    def __getattr__(self, name):
        return getattr(self._response, name)


class _AxiomSessionAdapter:
    """Wrap an httpx.Client so it behaves like a requests.Session.

    requests.Session().get(url, timeout=...) returns a requests.Response
    and raises requests.exceptions.ConnectionError / .Timeout for transport
    failures. httpx.Client().get(url, timeout=...) returns an
    httpx.Response and instead raises httpx.TransportError (connection
    refused, DNS failure, etc.) or httpx.TimeoutException (these are
    siblings in httpx's hierarchy, not one a subclass of the other, so both
    must be caught explicitly). This adapter performs the .get() call,
    translates those two failure modes into the requests-style stand-ins
    above, and wraps a successful response so raise_for_status() also
    raises the requests-style error. No exception is swallowed: every
    failure requests would have surfaced still reaches the caller, just
    re-raised as the corresponding stand-in type instead of the raw httpx
    type, exactly as requests's own exception would have looked.
    """

    def __init__(self, client):
        self._client = client

    def get(self, url, timeout=None, **kwargs):

        try:
            response = self._client.get(url, timeout=timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise AxiomTimeoutError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise AxiomConnectionError(str(exc)) from exc
        return _AxiomResponseAdapter(response)


def _axiom_default_session_wrapper():
    """Build a requests.Session()-like object backed by httpx.Client().

    requests.Session() returns an object whose .get() yields a
    requests.Response with requests-flavoured exceptions. httpx.Client()
    differs in three ways that matter to callers here: (1) its .get()
    returns an httpx.Response, not a requests.Response; (2) transport
    failures (refused connections, timeouts) raise httpx.TransportError /
    httpx.TimeoutException instead of requests.exceptions.ConnectionError /
    .Timeout; and (3) .raise_for_status() raises httpx.HTTPStatusError
    instead of requests.exceptions.HTTPError. This wrapper builds the
    httpx.Client and hands it to _AxiomSessionAdapter, which performs all
    three translations so callers keep seeing requests-style behaviour.
    """

    return _AxiomSessionAdapter(httpx.Client())


def _default_session():

    return _axiom_default_session_wrapper()


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def read_tag(self, tag: str) -> Mapping:
        resp = self.session.get(f"{self.base_url}/tags/{tag}", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return {
            "tag": tag,
            "value": data["value"],
            "quality": data.get("quality", "GOOD"),
        }

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        return [self.read_tag(t) for t in tags]
