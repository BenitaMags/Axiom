"""Small numeric helper shared by control modules."""


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high] using the same semantics as max(low, min(high, value))."""
    return max(low, min(high, value))
