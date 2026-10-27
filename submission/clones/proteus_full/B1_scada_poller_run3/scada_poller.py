"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _default_session():
    import httpx  # imported lazily so tests can inject a fake session

    return requests.Session()


def _default_session_wrapper():
    """Build a default HTTP client compatible with the ScadaPoller call sites.

    `requests.Session()` returns a `requests.Session` whose `.get(url,
    timeout=...)` accepts a single float that `requests` applies as an
    overall connect+read timeout, and whose object lifecycle is lenient --
    a `requests.Session` does not strictly need to be closed for short-lived
    polling scripts to behave correctly.

    `httpx.Client`, the replacement, differs in two ways that matter here:
    (1) its `timeout=` argument is normally an `httpx.Timeout` object that
    applies (possibly different) values across the connect/read/write/pool
    phases rather than a single blanket float, and (2) it holds pooled
    connections that are expected to be released via an explicit `.close()`
    call (or context-manager usage), unlike the more forgiving
    `requests.Session`.

    To bridge this, this wrapper constructs an `httpx.Timeout` from a single
    float so it mirrors the simple "one number for everything" semantics
    `requests` provided, keeping the per-call `timeout=self.timeout` float
    argument used in `ScadaPoller.read_tag` working unchanged (httpx also
    accepts a bare float there and converts it the same way). The returned
    `httpx.Client` is handed to `ScadaPoller`, which owns it for the
    lifetime of the poller; callers that want deterministic cleanup should
    call `.close()` on `ScadaPoller.session` when done, since httpx does not
    clean up pooled connections as leniently as `requests.Session` did.
    """

    default_timeout = httpx.Timeout(2.0)
    return httpx.Client(timeout=default_timeout)


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _default_session_wrapper()
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
