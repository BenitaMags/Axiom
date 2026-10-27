"""clamping -- small numeric helper module for range clamping."""


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp value into [lower, upper], matching max(lower, min(upper, value))."""
    return max(lower, min(upper, value))
