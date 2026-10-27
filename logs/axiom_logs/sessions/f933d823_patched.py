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
def _axiom_default_session_wrapper():
    """Build an HTTP client that mimics requests.Session() using httpx.

    `requests.Session()` returns a session object with `.get(url, timeout=...)`,
    `.raise_for_status()` (raising `requests.exceptions.HTTPError` on 4xx/5xx),
    and `.json()` on the response. httpx's sync equivalent is `httpx.Client()`,
    which supports the same `.get(url, timeout=...)` and `.json()` calls, but
    its `.raise_for_status()` raises `httpx.HTTPStatusError` instead -- a
    different exception type/hierarchy from requests. We do not attempt to
    translate that exception here (callers that only catch generic
    `Exception` are unaffected; callers that specifically catch
    `requests.exceptions.HTTPError` will need updating as part of the
    migration, which is expected and documented, not swallowed by this
    wrapper). We also give the client an explicit default timeout (5.0
    seconds, as a plain numeric value) since httpx treats "no timeout" and
    "requests-style default timeout" differently from requests' implicit
    defaults, and some tooling (e.g. bandit's B113 check) flags HTTP client
    construction that omits an explicit timeout value.
    """
    import httpx

    return httpx.Client(timeout=5.0)



def _default_session():
    return _axiom_default_session_wrapper()


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
