"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _requests_session_to_httpx_client_wrapper():
    """Build an httpx.Client that stands in for requests.Session().

    requests.Session() returns an object with no timeout configured by
    default -- each call site (e.g. read_tag) supplies its own per-request
    timeout via `session.get(url, timeout=...)`, and `.raise_for_status()`
    raises `requests.exceptions.HTTPError` on 4xx/5xx responses. It also
    follows redirects automatically by default.

    httpx.Client(), in contrast, defaults to a 5-second timeout on all verbs
    unless overridden, and does NOT follow redirects unless
    `follow_redirects=True` is passed at construction or per-request. Its
    `.raise_for_status()` raises `httpx.HTTPStatusError` instead of
    `requests.exceptions.HTTPError`.

    To keep the existing read_tag/poll call chain working unchanged --
    including their per-call `timeout=self.timeout` kwarg and their reliance
    on automatic redirect following -- this wrapper constructs the
    httpx.Client with an explicit default `timeout=5.0` (overridden per-call
    anyway) and `follow_redirects=True`, so `.get(url, timeout=...)` and
    `.raise_for_status()` remain drop-in compatible. Callers that need to
    distinguish HTTP error types should catch `httpx.HTTPStatusError`
    (httpx's equivalent of `requests.exceptions.HTTPError`); no exception is
    swallowed here.
    """
    import httpx

    return httpx.Client(timeout=5.0, follow_redirects=True)


def _default_session():
    return _requests_session_to_httpx_client_wrapper()


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
