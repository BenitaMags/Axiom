"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping

from scada_http_adapter import (
    HTTPError,
    ConnectionError,
    Timeout,
    _AxiomResponseWrapper,
    _AxiomHttpxSessionAdapter,
)


def _default_session():
    import httpx  # imported lazily so tests can inject a fake session

    return requests.Session()


def _axiom_default_session_wrapper():
    """Build a requests.Session-equivalent HTTP client backed by httpx.

    requests.Session() returns a session object supporting the blocking
    .get(url, timeout=...) call chain (.raise_for_status(), .json()) used by
    ScadaPoller.read_tag. httpx.Client() is used explicitly here -- never
    AsyncClient -- to keep that call synchronous. requests accepted a single
    float as its timeout; httpx.Timeout(2.0) applies that same float to the
    connect/read/write/pool phases so the default timeout behaves the same as
    requests' single-value default. The client is wrapped in
    _AxiomHttpxSessionAdapter so httpx's differing exception types are
    translated back into the local HTTPError/ConnectionError/Timeout that
    mirror requests' own exception contract.
    """
    import httpx

    client = httpx.Client(timeout=httpx.Timeout(2.0))
    return _AxiomHttpxSessionAdapter(client)


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _axiom_default_session_wrapper()
        self.timeout = timeout

    def _fetch(self, tag: str) -> Mapping:
        resp = self.session.get(f"{self.base_url}/tags/{tag}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _normalize(tag: str, data: Mapping) -> Mapping:
        return {
            "tag": tag,
            "value": data["value"],
            "quality": data.get("quality", "GOOD"),
        }

    def read_tag(self, tag: str) -> Mapping:
        data = self._fetch(tag)
        return self._normalize(tag, data)

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        return [self.read_tag(t) for t in tags]
