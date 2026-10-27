"""A small value object representing an inclusive numeric bound with a
clamping operation, used to replace repeated min/max clamping idioms."""

from typing import NamedTuple


class ClampedRange(NamedTuple):
    low: float
    high: float

    def clamp(self, value: float) -> float:
        return max(self.low, min(self.high, value))
