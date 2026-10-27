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
def _create_httpx_client_session():
    """Construct an httpx.Client to replace requests.Session().

    Original: requests.Session() returns a requests.Session object whose
    .get(url, timeout=<float>) method performs a per-call request with the
    given timeout, and whose response .raise_for_status() raises
    requests.exceptions.HTTPError on 4xx/5xx responses.

    New: httpx.Client() returns an httpx.Client object. httpx.Client supports
    the same .get(url, timeout=<float>) call signature (per-call float
    timeout override works the same way), and its response object supports
    .raise_for_status() and .json() with equivalent semantics -- however,
    on failure it raises httpx.HTTPStatusError instead of
    requests.exceptions.HTTPError. To keep behavior as close as possible to
    the original requests.Session(), this wrapper builds an httpx.Client
    with an explicit default Timeout configuration (mirroring the fact that
    requests.Session has no inherent default timeout, so callers must always
    pass timeout explicitly, which callers here already do).

    Returns
    -------
    httpx.Client
        A client object usable in place of requests.Session() for
        .get(url, timeout=...) / .raise_for_status() / .json() call
        patterns. Note: callers catching requests.exceptions.HTTPError must
        be updated to catch httpx.HTTPStatusError instead.
    """
    import httpx  # imported lazily so tests can inject a fake session

    default_timeout = httpx.Timeout(5.0)
    _spt_r1 = httpx.Client(timeout=default_timeout)
    return _spt_r1




def _default_session():
    _spt_r2 = _create_httpx_client_session()

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