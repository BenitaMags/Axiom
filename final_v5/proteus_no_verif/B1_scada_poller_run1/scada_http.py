"""Helper for constructing an httpx-backed session that mimics the subset
of ``requests.Session`` behavior that ``scada_poller`` depends on.

Kept as a separate module so the ``httpx`` import stays deferred: importing
``scada_poller`` (or this module) does not require ``httpx`` to be installed
unless a caller actually asks for the default session.
"""


class _ScadaHTTPXSession:
    def __init__(self, client):
        self._client = client

    def get(self, url, timeout=None):
        import httpx

        if isinstance(timeout, tuple):
            connect, read = timeout
            timeout = httpx.Timeout(connect=connect, read=read)
        return self._client.get(url, timeout=timeout)

    def close(self):
        self._client.close()


def build_httpx_session():
    """Create a ``_ScadaHTTPXSession`` wrapping a fresh ``httpx.Client``.

    ``httpx`` is imported lazily here so that modules importing this one
    (or ``scada_poller``) do not require ``httpx`` to be installed unless
    this factory is actually invoked.
    """
    import httpx  # imported lazily so tests can inject a fake session

    return _ScadaHTTPXSession(httpx.Client())
