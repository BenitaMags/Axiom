# CHOSEN-TARGET: first-party
"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is implemented
using the Python standard library (urllib), so no third-party HTTP dependency
is required.

The HTTP session is dependency-injected so the polling logic is testable without a
real network connection.
"""
import json
import urllib.error
import urllib.request
from typing import List, Mapping


class HTTPError(Exception):
    """Raised when an HTTP response indicates an error status code."""

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class Response:
    """Minimal stand-in for requests.Response, backed by urllib."""

    def __init__(self, status_code: int, body: bytes, url: str):
        self.status_code = status_code
        self._body = body
        self.url = url

    def json(self):
        return json.loads(self._body.decode("utf-8"))

    @property
    def text(self):
        return self._body.decode("utf-8")

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise HTTPError(
                f"HTTP error {self.status_code} for url: {self.url}", response=self
            )


class Session:
    """Minimal stand-in for requests.Session, backed by urllib."""

    def get(self, url: str, timeout: float = None) -> Response:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                body = resp.read()
                status_code = resp.getcode()
                return Response(status_code, body, url)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return Response(exc.code, body, url)
        except urllib.error.URLError as exc:
            raise HTTPError(f"Connection error for url: {url}: {exc}") from exc


def _default_session():
    return Session()


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
