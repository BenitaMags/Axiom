"""Shared helpers for PI-style controllers: clamping and mutable state carrier."""

from dataclasses import dataclass


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value into [lo, hi], matching max(lo, min(hi, value)) semantics."""
    return max(lo, min(hi, value))


@dataclass
class PIState:
    integral: float
    tripped: bool

    def reset(self):
        self.integral = 0.0
        self.tripped = False
