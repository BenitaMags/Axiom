"""Small numeric helper shared by control-style modules."""


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high], matching max(low, min(high, value))."""
    return max(low, min(high, value))
