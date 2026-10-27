"""pid_math -- tiny numeric helpers shared by control loops.

Kept in its own module so the clamping rule used for anti-windup and for
actuator saturation lives in exactly one place.
"""


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` into ``[lo, hi]``.

    Implemented as ``max(lo, min(hi, value))`` -- the same evaluation order
    used throughout the control code, which matters for edge cases such as
    ``NaN`` or inverted bounds (``lo > hi``).
    """
    return max(lo, min(hi, value))
