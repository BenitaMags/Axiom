"""Support helpers for PI-style controllers: clamping, gains, and interlocks."""

from dataclasses import dataclass


def clamp(value, low, high):
    """Clamp value into [low, high] using the same semantics as max(low, min(high, value))."""
    return max(low, min(high, value))


@dataclass(frozen=True)
class PIGains:
    kp: float
    ki: float

    def with_kp(self, kp):
        return PIGains(kp, self.ki)

    def with_ki(self, ki):
        return PIGains(self.kp, ki)


class InterlockState:
    """Latching over-pressure trip."""

    def __init__(self):
        self.tripped = False

    def observe(self, pressure, max_pressure):
        if pressure >= max_pressure:
            self.tripped = True
        return self.tripped

    def reset(self):
        self.tripped = False
