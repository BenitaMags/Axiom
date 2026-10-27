"""Support helpers for PI-style controllers: clamping and gain bundling."""

from dataclasses import dataclass, replace


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp value into [lower, upper] using the same semantics as
    max(lower, min(upper, value))."""
    return max(lower, min(upper, value))


@dataclass
class PiGains:
    kp: float
    ki: float

    def with_kp(self, kp: float) -> "PiGains":
        return replace(self, kp=kp)

    def with_ki(self, ki: float) -> "PiGains":
        return replace(self, ki=ki)

    def output(self, error: float, integral: float) -> float:
        return self.kp * error + self.ki * integral
