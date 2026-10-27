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
def _default_client():
    """Wrapper replacing requests.Session() with httpx.Client().

    Original: requests.Session() returns a requests.Session instance whose
    .get(url, timeout=...) returns a requests.Response with .raise_for_status()
    and .json() methods. requests.Session() itself takes no timeout argument --
    timeouts are only ever supplied per-request, and an omitted timeout on a
    later .get() call simply means "block forever" (requests' own default).

    New: httpx.Client() returns an httpx.Client instance. Unlike
    requests.Session(), a bare httpx.Client() with no default timeout is
    flagged by Bandit as B113 (call without timeout) because any call site
    that forgets to pass a per-request timeout would otherwise hang
    indefinitely. To close that gap we construct the client with an
    explicit numeric default timeout (in seconds, applied uniformly to the
    connect/read/write/pool phases) so the client itself is never
    implicitly unbounded. ScadaPoller.read_tag still passes its own
    per-call timeout argument, which overrides this default on every call,
    so behavior for existing call sites is unchanged -- this only removes
    the latent "hang forever" footgun for any future call that omits it.
    httpx.Client exposes the same .get(url, timeout=...) /
    .raise_for_status() / .json() call chain used by ScadaPoller.read_tag,
    so no further translation of the response object is required. The
    client is returned as a standalone, unclosed instance (mirroring the
    original Session's lifecycle, since existing code never calls .close()
    on the injected session).
    """
    import httpx  # imported lazily so tests can inject a fake client

    default_timeout_seconds = 2.0
    return httpx.Client(timeout=default_timeout_seconds)

# AXIOM CONNECTOR END









def _default_session():

    return _default_client()


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
