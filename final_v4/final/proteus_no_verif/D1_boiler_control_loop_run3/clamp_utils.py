"""Small numeric helpers shared by control modules."""


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp value into [lower, upper], matching max(lower, min(upper, value))."""
    return max(lower, min(upper, value))
