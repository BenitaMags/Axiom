"""pi_support -- shared helpers for PI-style controllers.

Small, reusable pieces extracted out of the main control module: a generic
clamp function and a value object bundling the tunable PI parameters.
"""

from dataclasses import dataclass


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value into [lo, hi], matching max(lo, min(hi, value)) semantics."""
    return max(lo, min(hi, value))


@dataclass
class PIParameters:
    setpoint: float
    kp: float
    ki: float
    max_pressure: float
    i_limit: float
