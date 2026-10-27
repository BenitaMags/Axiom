"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

This is the detection logic the LMA must rewrite into an equivalent but structurally
different form. A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.

Restructured (equivalent behaviour) to drive the state machine from an explicit
transition table rather than inline if/elif branching.
"""

NORMAL = "NORMAL"
ALARM = "ALARM"


class Hysteresis:
    def __init__(self, high: float, low: float):
        if low >= high:
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = NORMAL
        self._transitions = {
            NORMAL: lambda value: ALARM if value >= self.high else NORMAL,
            ALARM: lambda value: NORMAL if value <= self.low else ALARM,
        }

    def update(self, value: float) -> str:
        self.state = self._transitions[self.state](value)
        return self.state
