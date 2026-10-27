"""Small numeric helper used by control.py."""


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value into [lo, hi] using the same semantics as max(lo, min(hi, value))."""
    return max(lo, min(hi, value))
