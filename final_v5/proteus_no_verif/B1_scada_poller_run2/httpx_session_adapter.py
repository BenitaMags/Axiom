"""Adapter layer bridging httpx's API/exception surface to requests-like usage.

# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: requests → httpx
# The following functions bridge API incompatibilities between
# requests and httpx. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
"""


class _HttpxResponseAdapter:
    def __init__(self, response):
        self._response = response

    def raise_for_status(self):
        import httpx
        import requests

        try:
            self._response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise requests.exceptions.HTTPError(str(exc), response=self._response) from exc

    def json(self):
        return self._response.json()

    def __getattr__(self, name):
        return getattr(self._response, name)


class _HttpxSessionAdapter:
    def __init__(self, client):
        self._client = client

    def get(self, url, timeout=None, **kwargs):
        import httpx
        import requests

        try:
            resp = self._client.get(url, timeout=timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise requests.exceptions.Timeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise requests.exceptions.ConnectionError(str(exc)) from exc
        return _HttpxResponseAdapter(resp)


def build_httpx_session_adapter():
    """Construct a requests.Session-compatible object backed by httpx.Client().

    Original call: requests.Session() -- returns a requests.Session whose .get()
    returns a requests.Response. Response.raise_for_status() raises
    requests.exceptions.HTTPError on 4xx/5xx responses; connection/timeout failures
    raise requests.exceptions.ConnectionError / requests.exceptions.Timeout.

    New behavior: builds an httpx.Client() (the synchronous client, not
    AsyncClient) and wraps it so that .get() still returns an object exposing
    .raise_for_status() and .json(), but translates httpx's exception types back
    into the requests exception types the original code (and any of its callers)
    expect:
      * httpx.HTTPStatusError      -> requests.exceptions.HTTPError
      * httpx.TimeoutException     -> requests.exceptions.Timeout
      * other httpx.TransportError -> requests.exceptions.ConnectionError

    This keeps read_tag's `resp.raise_for_status()` / `resp.json()` usage working
    unchanged while preserving the exception contract established by `requests`.
    """
    import httpx

    return _HttpxSessionAdapter(httpx.Client())
