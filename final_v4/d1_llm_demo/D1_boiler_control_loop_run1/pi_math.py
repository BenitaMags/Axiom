"""pi_math -- shared numeric helpers for PI-style controllers."""

from dataclasses import dataclass


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp value to [lower, upper].

    Evaluated as max(lower, min(upper, value)) to match the exact
    short-circuit/NaN behaviour of Python's built-in min/max chaining.
    """
    return max(lower, min(upper, value))


@dataclass
class Accumulator:
    """A running sum with a symmetric clamp, used for integral terms."""

    value: float = 0.0

    def add(self, amount: float, limit: float) -> float:
        """Add `amount`, clamp to [-limit, limit], and return the new value."""
        self.value += amount
        self.value = clamp(self.value, -limit, limit)
        return self.value

    def reset(self) -> None:
        self.value = 0.0
