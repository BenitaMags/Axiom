"""clamp_util -- small numeric helper shared by control logic."""


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp value to the inclusive range [lower, upper].

    Mirrors the semantics of max(lower, min(upper, value)), including
    behaviour on NaN and inverted bounds (lower > upper).
    """
    return max(lower, min(upper, value))
