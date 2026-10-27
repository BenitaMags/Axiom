"""pi_support -- small reusable value objects for PI-style controllers."""

from typing import NamedTuple


class ClampedRange(NamedTuple):
    low: float
    high: float

    def clamp(self, value: float) -> float:
        return max(self.low, min(self.high, value))
