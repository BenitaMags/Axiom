"""Shared helpers for PI-style controllers."""

from dataclasses import dataclass


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high], matching max(low, min(high, value))."""
    return max(low, min(high, value))


@dataclass
class PIParameters:
    setpoint: float
    kp: float
    ki: float
    max_pressure: float
    i_limit: float
