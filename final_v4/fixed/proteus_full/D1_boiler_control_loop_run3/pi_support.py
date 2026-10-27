"""Shared helpers for PI-style controllers."""

from dataclasses import dataclass


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high], matching max(low, min(high, value))."""
    return max(low, min(high, value))


@dataclass
class PIState:
    integral: float
    tripped: bool

    def reset(self) -> None:
        self.integral = 0.0
        self.tripped = False
