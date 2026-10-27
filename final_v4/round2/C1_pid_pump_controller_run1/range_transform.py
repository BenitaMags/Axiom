"""range_transform -- generic linear interpolation helper for I/O scaling."""

from dataclasses import dataclass
from typing import Tuple


@dataclass
class LinearRange:
    """Maps values linearly from one numeric range onto another."""

    source: Tuple[float, float]
    target: Tuple[float, float]

    def scale(self, value):
        lo, hi = self.source
        a, b = self.target
        return a + (value - lo) * (b - a) / (hi - lo)

    def scale_clamped_rounded(self, value) -> int:
        lo, hi = self.source
        clamped = max(lo, min(hi, value))
        return int(round(self.scale(clamped)))
