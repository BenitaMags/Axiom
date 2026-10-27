# CHOSEN-TARGET: first-party
"""client -- SCADA HTTP client for the pump station.


identical sessions/timeouts/exceptions -> glue required). Session is injected.
"""

import json as _json
import urllib.error
import urllib.request


class HTTPError(Exception):
    """Raised when an HTTP response has a status code indicating failure."""

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class Response:
    """Minimal stand-in for requests.Response, backed by urllib."""

    def __init__(self, status_code: int, body: bytes, url: str = ""):
        self.status_code = status_code
        self._body = body
        self.url = url

    def json(self):
        return _json.loads(self._body.decode("utf-8")) if self._body else None

    @property
    def text(self):
        return self._body.decode("utf-8") if self._body else ""

    @property
    def content(self):
        return self._body

    def raise_for_status(self):
        if 400 <= self.status_code:
            raise HTTPError(
                f"{self.status_code} Error for url: {self.url}", response=self
            )


class Session:
    """Minimal stand-in for requests.Session, backed by urllib."""

    def get(self, url, timeout=None, params=None):
        if params:
            from urllib.parse import urlencode

            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        return self._send(req, timeout)

    def post(self, url, json=None, timeout=None):
        data = None
        headers = {}
        if json is not None:
            data = _json.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        return self._send(req, timeout)

    def _send(self, req, timeout):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return Response(resp.getcode(), body, url=req.full_url)
        except urllib.error.HTTPError as e:
            body = e.read()
            return Response(e.code, body, url=req.full_url)
        except urllib.error.URLError as e:
            raise ConnectionError(str(e)) from e


def _default_session():
    return Session()


class ScadaClient:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def get_demand(self) -> float:
        r = self.session.get(f"{self.base}/demand", timeout=self.timeout)
        r.raise_for_status()
        return r.json()["demand"]

    def get_runtimes(self) -> list:
        r = self.session.get(f"{self.base}/runtimes", timeout=self.timeout)
        r.raise_for_status()
        return r.json()["runtimes"]

    def command(self, pump: int, on: bool) -> int:
        r = self.session.post(f"{self.base}/pump/{pump}", json={"on": on},
                              timeout=self.timeout)
        r.raise_for_status()
        return getattr(r, "status_code", 200)
