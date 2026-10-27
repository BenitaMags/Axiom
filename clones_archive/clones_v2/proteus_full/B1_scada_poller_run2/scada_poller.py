"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _axiom_default_client_wrapper(timeout: float = None):
    """Build an httpx.Client that behaves like a requests.Session().

    ``requests.Session()`` has no default per-request timeout (a request will
    block forever unless a timeout is explicitly passed to ``.get()``/etc.) and
    it *always* follows HTTP redirects transparently.

    ``httpx.Client()`` differs on both points: it defaults to a 5 second
    timeout for every request unless overridden, and it does **not** follow
    redirects unless ``follow_redirects=True`` is passed to the constructor
    (or to each call).

    This wrapper constructs an ``httpx.Client`` with ``follow_redirects=True``
    and a timeout equal to the caller-supplied float (or ``None``, matching
    requests' "wait forever" default), so code written against the old
    ``requests.Session()`` semantics keeps working unchanged -- including the
    common pattern of passing a per-call ``timeout=`` to ``.get()``.
    """
    import httpx

    return httpx.Client(follow_redirects=True, timeout=timeout)


def _default_session():

    return _axiom_default_client_wrapper()


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
