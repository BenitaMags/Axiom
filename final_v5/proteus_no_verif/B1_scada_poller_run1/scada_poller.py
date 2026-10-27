"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping

from scada_http import build_httpx_session


def _scada_httpx_client_factory():
    """Build an object that mimics the pieces of ``requests.Session`` this
    module relies on, backed by ``httpx.Client``.

    ``requests.Session().get(url, timeout=...)`` accepted a plain float
    ("both connect and read timeout") or a ``(connect, read)`` tuple, and
    returned a ``requests.Response`` whose ``.raise_for_status()`` raised
    ``requests.exceptions.HTTPError`` and whose ``.json()`` parsed the body.

    ``httpx.Client.get(url, timeout=...)`` instead expects ``None``, a bare
    float, or an ``httpx.Timeout`` instance -- it does not understand a
    ``(connect, read)`` tuple. Its response's ``.raise_for_status()`` raises
    ``httpx.HTTPStatusError`` rather than the requests exception type, and
    connection-level failures raise ``httpx.HTTPError`` subclasses instead of
    ``requests.exceptions.ConnectionError``/``Timeout``.

    This wrapper normalizes only the incompatible piece -- the timeout
    argument -- by translating a tuple into ``httpx.Timeout(connect=...,
    read=...)`` before delegating to the real client. It deliberately does
    NOT swallow or re-map ``raise_for_status``/connection errors: the call
    site here never catches a specific exception type, it just lets
    failures propagate, so letting httpx's own exceptions propagate
    unchanged preserves that behavior exactly (rather than inventing a
    translation nobody asked for).
    """
    return build_httpx_session()  # imports httpx lazily inside the helper


def _default_session():
    return _scada_httpx_client_factory()


def _normalize_tag_record(tag: str, data: Mapping) -> Mapping:
    return {
        "tag": tag,
        "value": data["value"],
        "quality": data.get("quality", "GOOD"),
    }


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def _tag_url(self, tag: str) -> str:
        return f"{self.base_url}/tags/{tag}"

    def read_tag(self, tag: str) -> Mapping:
        resp = self.session.get(self._tag_url(tag), timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return _normalize_tag_record(tag, data)

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        records = []
        for t in tags:
            records.append(self.read_tag(t))
        return records
