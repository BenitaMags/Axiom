"""saturation -- a reusable saturation element shared by the control and transport layers.

The reference program clamps with nested ``max(lo, min(hi, x))`` expressions written out
at every use.  Here the limits are a value object and clamping is rail detection.
"""
from typing import NamedTuple


class Rails(NamedTuple):
    """A pair of limits; ``pin`` holds a value on whichever rail it runs into."""
    low: float
    high: float

    def pin(self, value):
        # Decide on the upper rail first so that every input -- including NaN, which
        # compares false against both rails, and inverted limits -- lands exactly where
        # the nested max/min form puts it.
        if value < self.high:
            return value if value > self.low else self.low
        return self.high if self.high > self.low else self.low


VALVE_TRAVEL = Rails(0.0, 100.0)  # percent open
