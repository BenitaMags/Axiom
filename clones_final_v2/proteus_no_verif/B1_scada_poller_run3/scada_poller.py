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
    """Construct an httpx.Client configured to behave like requests.Session().

    ``requests.Session()`` returns a session object whose ``.get()`` method
    accepts a plain float/tuple ``timeout`` and returns a ``Response`` with
    ``.raise_for_status()`` (raising ``requests.exceptions.HTTPError``) and
    ``.json()``. ``httpx.Client()`` is the closest equivalent: it also pools
    connections and exposes ``.get()``/``.raise_for_status()``/``.json()``
    with compatible signatures, so no per-call shimming is needed at the
    request layer. The differences are structural:

      * httpx.Client supports fine-grained connect/read/write/pool timeouts,
        but a single float (as passed by call sites here) is still accepted
        and applied to all phases, matching the simpler requests behavior.
      * httpx.Client must be explicitly closed (it holds an underlying
        connection pool), unlike requests.Session which can simply be
        garbage collected. We construct a plain httpx.Client() here (no
        context manager) since the returned object is stored on the poller
        for its lifetime and reused across polls, mirroring how the
        requests.Session was used; callers that need deterministic cleanup
        should call ``.close()`` on the returned client themselves.
      * httpx raises ``httpx.HTTPStatusError`` from ``raise_for_status()``
        instead of ``requests.exceptions.HTTPError``; call sites that only
        rely on ``raise_for_status()`` raising *some* exception on error
        status codes are unaffected.
    """
    import httpx  # imported lazily so tests can inject a fake session
    _spt_r1 = httpx.Client()

    return _spt_r1


def _default_session():
    _spt_r2 = _axiom_default_session_wrapper()
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
