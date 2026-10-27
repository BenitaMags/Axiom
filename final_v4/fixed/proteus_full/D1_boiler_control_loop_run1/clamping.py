"""clamping -- small helper for bounding a value within [lo, hi]."""


def clamp(value: float, lo: float, hi: float) -> float:
    """Return value bounded to the closed interval [lo, hi].

    Mirrors the behaviour of max(lo, min(hi, value)), including how it
    handles NaN and inverted bounds (where lo > hi).
    """
    return max(lo, min(hi, value))
