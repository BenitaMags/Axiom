"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

This is the detection logic the LMA must rewrite into an equivalent but structurally
different form. A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.
"""

NORMAL = "NORMAL"
ALARM = "ALARM"


def _transitions(high: float, low: float):
    """Return a dict mapping current state to a predicate/next-state pair."""
    return {
        NORMAL: (lambda value: value >= high, ALARM),
        ALARM: (lambda value: value <= low, NORMAL),
    }


class Hysteresis:
    def __init__(self, high: float, low: float):
        if low >= high:
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = NORMAL
        self._transitions = _transitions(high, low)

    def update(self, value: float) -> str:
        predicate, next_state = self._transitions[self.state]
        if predicate(value):
            self.state = next_state
        return self.state
