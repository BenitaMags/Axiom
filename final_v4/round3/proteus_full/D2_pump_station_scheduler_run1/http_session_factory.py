"""Factory helpers for constructing the default HTTP session/client.

Kept as a separate module so the HTTP client substitution target
(`requests` -> `httpx`) is isolated from the SCADA client's own logic.
"""


def build_default_session():
    """Construct the HTTP client used when no session/client is injected.

    requests' ``Session()`` constructor defaults to following HTTP redirects
    automatically (``allow_redirects=True`` is the default for the
    convenience methods built on top of a Session). httpx.Client(), by
    contrast, defaults to NOT following redirects
    (``follow_redirects=False``) unless told otherwise. Callers of this
    module rely on the old requests-derived redirect-following behavior
    (e.g. a pump controller that may be reachable via a redirecting proxy),
    so this wrapper passes ``follow_redirects=True`` explicitly to
    httpx.Client to preserve parity with the previous requests.Session()
    default.

    requests' ``Session()`` also has no notion of a constructor-level
    timeout at all -- a plain ``requests.Session()`` with no timeout passed
    to a request would simply block forever, which static analysis (Bandit
    B113) flags as a hazard. httpx.Client() makes this explicit: if no
    timeout is given to the constructor, it falls back to its own default
    (5 seconds), but to avoid relying on an implicit default and to satisfy
    the "always set a timeout" contract, we set an explicit default timeout
    here on the Client itself. This is a fallback only: every call site in
    this module already passes its own ``timeout=self.timeout`` argument
    per-request, and that per-call value still takes precedence over this
    constructor-level default. No other behavior is changed: lifecycle and
    closing of the returned client is left to the existing code paths,
    exactly as it was for the requests.Session() this replaces.
    """
    import httpx  # lazy import to mirror the previous lazy `import requests`

    default_timeout = httpx.Timeout(10.0, connect=5.0)
    return httpx.Client(follow_redirects=True, timeout=default_timeout)
