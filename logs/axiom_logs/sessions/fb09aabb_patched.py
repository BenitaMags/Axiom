"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_session_factory_wrapper():
    """Build an httpx.Client() configured to behave like requests.Session().

    ``requests.Session()`` returns an object with connection pooling enabled,
    no default timeout (blocks indefinitely unless a per-request timeout is
    given), and transparent following of HTTP redirects for the methods the
    polling code relies on (``get``/``raise_for_status``/``json``).

    ``httpx.Client()`` differs on two defaults that matter here:

    * it does NOT follow redirects unless ``follow_redirects=True`` is passed
      -- a plain construction would silently change behavior for a gateway
      that redirects tag reads;
    * it applies its own ``httpx.Timeout`` object (separate connect/read/
      write/pool timeouts) rather than a single float, so we set an explicit
      ``httpx.Timeout`` here to keep default blocking behavior similar to
      requests when a call site does not override the timeout.

    This wrapper builds the client with those two adjustments so it is a
    drop-in replacement for ``requests.Session()`` for this module's call
    sites. It does not catch or translate exceptions -- connection errors
    and HTTP errors from the returned client still propagate to callers
    exactly as httpx raises them.
    """
    import httpx

    _axiom_default_timeout = httpx.Timeout(
        connect=5.0, read=5.0, write=5.0, pool=5.0
    )
    return httpx.Client(follow_redirects=True, timeout=_axiom_default_timeout)


def _default_session():

    return _axiom_session_factory_wrapper()


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
