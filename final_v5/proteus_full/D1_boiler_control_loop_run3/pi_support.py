"""Support helpers for PI-style controllers: shared value objects and math."""

from dataclasses import dataclass


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high], matching max(low, min(high, value))."""
    return max(low, min(high, value))


@dataclass
class PIParameters:
    """Bundles the tuning and safety parameters for a PI boiler controller."""
    setpoint: float
    kp: float
    ki: float
    max_pressure: float
    i_limit: float
