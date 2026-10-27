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
def _create_http_client():
    """Construct an httpx.Client to replace a requests.Session().

    Original: `requests.Session()` returns a requests.Session object whose
    `.get(url, timeout=...)` returns a requests.Response with `.raise_for_status()`
    and `.json()` methods, and which does not require explicit timeout defaults
    (per-request timeout is passed explicitly by callers in this module).

    New: `httpx.Client()` returns an httpx.Client object. httpx.Client's `.get()`
    method, `.raise_for_status()`, and `.json()` behave equivalently for the
    call patterns used in this module (a per-call `timeout=` kwarg overrides any
    client default), so no signature translation is needed at the call site.
    A default httpx.Timeout is set here only as a safety net; callers in this
    module always pass an explicit timeout to `.get()`, which takes precedence.

    Note: unlike a `requests.Session` (which is also typically closed explicitly
    or via context manager), this httpx.Client is not used as a context manager
    here and must be closed explicitly by the owner (e.g. via `.close()`) when
    no longer needed to release its underlying connection pool; this wrapper
    does not perform that cleanup since the original code did not either.
    """
    import httpx
    _spt_r1 = httpx.Client(timeout=httpx.Timeout(2.0))

    return _spt_r1
# AXIOM CONNECTOR END



def _default_session():
    _spt_r2 = _create_http_client()

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