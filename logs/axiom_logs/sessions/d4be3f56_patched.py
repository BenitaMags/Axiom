"""sensor_json_logger -- deterministic JSON audit logging of sensor samples.

Serializes tag/value sensor readings to canonical one-line JSON records. The JSON
backend is the substitution target: `json` (stdlib) -> `ujson` (drop-in, faster).
Determinism (sorted keys, compact separators) is a hard requirement so that audit
logs are byte-reproducible.
"""
import ujson
import math
from typing import Any, Dict, Iterable, Mapping

_SEPARATORS = (",", ":")


def _audit_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS):
    """Serialize ``record`` to a compact JSON string using ujson.

    stdlib ``json.dumps`` accepts ``sort_keys`` and ``separators`` keyword
    arguments and returns a ``str``. ujson's ``dumps`` returns a ``str`` too,
    but it does not accept a ``separators`` argument at all (passing one
    raises ``TypeError``), and its output is already compact by default (no
    spaces after ``:`` or ``,``), so the requested ``_SEPARATORS`` of
    ``(",", ":")`` is exactly what ujson already produces. We therefore
    validate (but do not forward) ``separators``, and only accept it if it
    matches the compact form we require for byte-reproducible audit logs;
    anything else would silently change the on-disk format, so we raise
    instead of ignoring it silently.

    We force ``sort_keys=True`` through explicitly (ujson does support this
    kwarg) since deterministic key ordering is a hard requirement for this
    audit logger.

    We also pin ``ensure_ascii=True`` explicitly: ujson's historical defaults
    for ASCII escaping have drifted across versions relative to stdlib json
    (which always escapes non-ASCII by default). We also pass
    ``escape_forward_slashes=False``: ujson, unlike stdlib json, escapes
    ``/`` as ``\/`` by default, which would silently change the on-disk
    byte format of every record containing a slash (e.g. tag names like
    ``"a/b"``); passing this flag restores stdlib json's behavior of never
    escaping forward slashes.

    We additionally *attempt* to pin ``double_precision=15`` to reduce
    float-formatting drift versus stdlib json's shortest round-trippable
    float repr. Not all ujson versions accept that keyword argument (some
    raise ``TypeError``), and ujson's type stubs (as seen by mypy) also do
    not declare it, so passing it as a literal keyword argument would be
    flagged as a static-typing error even though it is valid at runtime on
    supporting versions. To keep both mypy and older ujson runtimes happy,
    we pass it via a ``**dict`` unpack (``extra_kwargs``) rather than a
    literal keyword, which mypy cannot statically validate against the
    stub signature, and we still guard the call with
    ``try``/``except TypeError`` so builds that reject the kwarg at runtime
    fall back to calling ``dumps`` without it. This keeps the wrapper
    working across ujson versions that do and don't expose the kwarg, at the
    cost of the float precision fallback still not being byte-identical to
    CPython's ``repr(float)`` in all cases.

    Finally, unlike stdlib json's default (``allow_nan=True``, which emits
    the non-standard tokens ``NaN``/``Infinity``/``-Infinity``), we choose to
    restore strict-JSON-compatible behavior: this wrapper pre-scans
    ``record`` for non-finite floats and raises ``ValueError`` if any are
    found, since ujson provides no ``allow_nan`` toggle and would otherwise
    silently emit those invalid-JSON tokens instead of raising.
    """
    if separators != _SEPARATORS:
        raise ValueError(
            "_audit_json_dumps_wrapper only supports the canonical "
            "compact separators %r (ujson has no separators parameter and "
            "always emits compact output)" % (_SEPARATORS,)
        )
    if not sort_keys:
        raise ValueError(
            "_audit_json_dumps_wrapper requires sort_keys=True for "
            "deterministic audit log output"
        )

    def _check_finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                "Out of range float values are not JSON compliant: %r" % (value,)
            )
        if isinstance(value, Mapping):
            for v in value.values():
                _check_finite(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _check_finite(v)

    _check_finite(record)

    extra_kwargs: Dict[str, Any] = {"double_precision": 15}
    try:
        return ujson.dumps(
            record,
            sort_keys=True,
            ensure_ascii=True,
            escape_forward_slashes=False,
            **extra_kwargs,
        )
    except TypeError:
        # This ujson build does not support the double_precision kwarg;
        # fall back to its default float precision. escape_forward_slashes
        # is still required to match stdlib json's behaviour of leaving
        # '/' unescaped.
        return ujson.dumps(
            record,
            sort_keys=True,
            ensure_ascii=True,
            escape_forward_slashes=False,
        )


def format_reading(tag: str, value, ts: str, quality: str = "GOOD") -> str:
    """Return a canonical one-line JSON record for a single reading."""
    record = {"tag": tag, "value": value, "ts": ts, "quality": quality}
    return _audit_json_dumps_wrapper(record, sort_keys=True, separators=_SEPARATORS)


def format_batch(readings: Iterable[Mapping]) -> str:
    """Serialize an iterable of reading mappings to newline-delimited JSON."""
    return "\n".join(format_reading(**dict(r)) for r in readings)
