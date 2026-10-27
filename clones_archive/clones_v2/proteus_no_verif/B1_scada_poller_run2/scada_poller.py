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
    import httpx  # noqa: F401 -- import preserved per migration lock; the actual
    # client object is now constructed by _axiom_default_client_wrapper() below, which
    # uses httpx instead of this module.

    return _axiom_default_client_wrapper()


def _axiom_default_client_wrapper():
    """Build the default HTTP client used when no session is injected.

    ``requests.Session()`` returns a ``requests.Session`` object with a connection
    pool and *no* default timeout (requests only applies a timeout if you pass one
    on each call). ``httpx.Client()`` instead requires (or defaults to) an
    ``httpx.Timeout`` object rather than a bare float/tuple, and its constructor
    semantics for connection pooling differ slightly (e.g. ``limits=`` instead of
    requests' ``HTTPAdapter`` pool_connections/pool_maxsize knobs).

    To keep ``ScadaPoller`` -- which calls ``.get()``, ``.raise_for_status()`` and
    ``.json()`` on whatever session object it is given -- working unchanged, this
    wrapper builds an ``httpx.Client()`` configured with an explicit default
    ``httpx.Timeout`` (mirroring the "no surprise hangs" behavior callers expect
    from a requests.Session with per-call timeouts) so the returned object is a
    drop-in replacement for the old ``requests.Session()`` default factory.
    """

    default_timeout = httpx.Timeout(5.0, connect=5.0)
    return httpx.Client(timeout=default_timeout)


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
