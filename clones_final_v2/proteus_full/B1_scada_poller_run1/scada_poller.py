"""scada_poller -- periodic polling of a SCADA REST gateway.

Reads tag values from an HTTP SCADA gateway. The HTTP client is the substitution
target: `requests` -> `httpx`. The two libraries are similar but *not* identical
(session construction, timeout semantics, and raised exception types differ), so a
clone requires glue code -- hence Category B.

The HTTP session is dependency-injected so the polling logic is testable without a
live gateway or the `requests`/`httpx` package installed.
"""
from typing import List, Mapping


def _default_httpx_client_wrapper():
    """Build the HTTP client object used by ``ScadaPoller`` when the caller
    doesn't supply one.

    ``requests.Session()`` returns a session object that transparently pools
    TCP connections across requests and is forgiving about not being closed
    explicitly (its connections are cleaned up on garbage collection, if a
    bit later than ideal). ``httpx.Client()`` is httpx's equivalent pooling
    client, but its constructor semantics are different (e.g. timeouts are
    configured via ``httpx.Timeout`` objects with separate connect/read/write
    values instead of a single float or ``(connect, read)`` tuple), and,
    critically, an ``httpx.Client`` instance *must* be closed explicitly (or
    used as a context manager) -- unlike ``requests.Session`` it does not
    reliably release pooled connections on garbage collection, so leaving it
    unclosed is a real resource leak in a long-running poller.

    This wrapper creates the ``httpx.Client()`` with defaults equivalent to
    the previous ``requests.Session()`` usage (no extra timeout is baked in
    here since callers currently pass ``timeout=`` per request, and httpx
    accepts a plain float there the same way requests does). Because the
    returned client needs an explicit ``.close()``, ``ScadaPoller`` has been
    turned into a context manager (see ``__enter__``/``__exit__``/``close``)
    so that resource is always released deterministically.
    """
    import httpx

    return httpx.Client()


def _default_session():

    return _default_httpx_client_wrapper()


class ScadaPoller:
    def __init__(self, base_url: str, session=None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else _default_session()
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        """Release the underlying HTTP client's connection pool.

        Unlike ``requests.Session``, an ``httpx.Client`` (as produced by
        ``_default_httpx_client_wrapper``) does not reliably free its pooled
        connections on garbage collection, so callers (or this class's own
        context-manager protocol) must call ``close()`` explicitly to avoid
        leaking sockets.
        """
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

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
