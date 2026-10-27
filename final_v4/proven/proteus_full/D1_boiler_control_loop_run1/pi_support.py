"""pi_support -- shared helpers for PI-style controllers.

Provides a small value object bundling PI controller parameters together,
plus a generic clamp helper used for anti-windup and output limiting.
"""

from dataclasses import dataclass


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to the inclusive range [low, high].

    Mirrors the exact semantics of ``max(low, min(high, value))``:
    if ``low > high`` the result follows the same evaluation order as the
    original expression (min applied first, then max).
    """
    return max(low, min(high, value))


@dataclass
class PIParameters:
    """Bundles the tunable parameters of a PI controller with an interlock."""
    setpoint: float
    kp: float
    ki: float
    max_pressure: float
    i_limit: float
