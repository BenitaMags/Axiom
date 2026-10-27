"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_default_session_wrapper():
    """Build a drop-in replacement for ``requests.Session()`` on top of httpx.

    ``requests.Session()`` returns a ``Session`` object whose ``.get(url,
    timeout=<float>)`` accepts a plain float/tuple timeout and whose
    ``.raise_for_status()`` raises ``requests.exceptions.HTTPError`` on 4xx/5xx
    responses. It also manages its own connection pool with permissive
    defaults (e.g. no timeout at all unless one is passed per-request).

    ``httpx`` has no ``Session`` class -- the equivalent is ``httpx.Client()``,
    and it differs in a few ways that matter for this polling code:
      * ``httpx.Client`` timeouts are expressed via an ``httpx.Timeout`` object
        with separate connect/read/write/pool phases rather than a single
        float, though it still happily accepts a bare float per-call the way
        ``ScadaPoller.read_tag`` calls ``self.session.get(url,
        timeout=self.timeout)``.
      * ``httpx``'s ``.raise_for_status()`` raises ``httpx.HTTPStatusError``
        instead of ``requests.exceptions.HTTPError``.
      * ``httpx.Client`` should be explicitly closed (or used as a context
        manager), unlike ``requests.Session`` which cleans up more casually.

    This wrapper constructs an ``httpx.Client`` configured with a sane default
    ``httpx.Timeout`` (so callers that never pass a timeout still get
    requests-like "just works" behavior) while leaving per-call timeouts
    (as used by ``ScadaPoller``) free to override it. The returned client's
    ``.get(...)`` and ``.raise_for_status()`` methods are used exactly like
    ``requests.Session``'s in the rest of this module, so no other code needs
    to change beyond routing session construction through this function.
    """
    import httpx

    return httpx.Client(timeout=httpx.Timeout(5.0))


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
