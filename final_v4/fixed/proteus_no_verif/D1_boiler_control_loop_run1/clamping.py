"""clamping -- small numeric helper for bounding values within a range."""


def clamp(value: float, lower: float, upper: float) -> float:
    """Return value restricted to the closed interval [lower, upper].

    Mirrors the semantics of max(lower, min(upper, value)).
    """
    return max(lower, min(upper, value))
