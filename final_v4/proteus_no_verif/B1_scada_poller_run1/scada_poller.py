"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


# ──────────────────────────────────────────────────────────────────────
# AXIOM CONNECTOR: requests → httpx
# The following functions bridge API incompatibilities between
# requests and httpx. They are injected by the AXIOM
# connector agent and called in place of the original API.
# ──────────────────────────────────────────────────────────────────────
def _create_httpx_session():
    """Construct an HTTP client to replace `requests.Session()`.

    Original: `requests.Session()` returns a `requests.Session` object whose
    `.get(url, timeout=...)` returns a `requests.Response` with
    `.raise_for_status()` (raises `requests.HTTPError` on 4xx/5xx) and
    `.json()` (parses the body as JSON).

    New: `httpx.Client()` returns an `httpx.Client` object. Its `.get(url,
    timeout=...)` returns an `httpx.Response` object that also exposes
    `.raise_for_status()` (raises `httpx.HTTPStatusError` on 4xx/5xx) and
    `.json()`. No conversion of inputs/outputs is required for the call
    pattern used by ScadaPoller (`session.get(url, timeout=...)` followed by
    `.raise_for_status()` and `.json()`), since httpx.Client mirrors that
    surface directly. This wrapper simply centralizes construction with
    equivalent default verify/cert/timeout behavior (httpx defaults to SSL
    verification enabled, matching requests' default), and returns the
    httpx.Client instance (which also supports use as a context manager and
    `.close()`, just like requests.Session).
    """
    import httpx  # imported lazily so tests can inject a fake session
    _spt_r1 = httpx.Client(verify=True)

    return _spt_r1

# AXIOM CONNECTOR END



def _default_session():
    _spt_r2 = _create_httpx_session()

    return _spt_r2


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        if not (session is not None):
            self.session = _default_session()
        else:
            self.session = session
        self.timeout = timeout

    def read_tag(self, tag: str) -> Mapping:
        resp = self.session.get(f"{self.base_url}/tags/{tag}", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        _spt_r3 = {
            "tag": tag,
            "value": data["value"],
            "quality": data.get("quality", "GOOD"),
        }
        return _spt_r3

    def poll(self, tags) -> List[Mapping]:
        """Read a list of tags, returning one normalized record per tag."""
        _spt_r4 = [self.read_tag(t) for t in tags]
        return _spt_r4