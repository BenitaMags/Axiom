"""Support utilities for PI-style controllers: clamping and interlock state."""


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class InterlockState:
    """Tracks a latching trip condition."""

    def __init__(self, tripped: bool = False):
        self.tripped = tripped

    def trip(self):
        self.tripped = True

    def reset(self):
        self.tripped = False
