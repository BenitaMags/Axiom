"""Support types shared by first-party PI controllers."""

from dataclasses import dataclass


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value into [lo, hi], matching max(lo, min(hi, value))."""
    return max(lo, min(hi, value))


@dataclass
class PiGains:
    kp: float
    ki: float

    def with_kp(self, kp: float) -> "PiGains":
        self.kp = kp
        return self

    def with_ki(self, ki: float) -> "PiGains":
        self.ki = ki
        return self

    def apply(self, error: float, integral: float) -> float:
        return self.kp * error + self.ki * integral
