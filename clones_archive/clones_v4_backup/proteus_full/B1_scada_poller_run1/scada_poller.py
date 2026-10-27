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
    """Construct the default HTTP client used by ScadaPoller.

    ``requests.Session()`` (the original API) creates a session object with no
    default timeout -- requests will wait indefinitely on a request unless a
    timeout is explicitly passed to each call. ``httpx.Client()`` (the
    replacement library's equivalent) defaults to a 5-second timeout on all
    requests made through it, which would silently change behavior for any
    call site that doesn't pass its own explicit timeout.

    This wrapper builds an ``httpx.Client()`` with ``timeout=None`` so that,
    absent a per-request timeout override, the client blocks indefinitely --
    matching requests' historical default -- instead of adopting httpx's
    5-second default and causing spurious timeouts in production.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return httpx.Client(timeout=None)


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
