"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

This is the detection logic the LMA must rewrite into an equivalent but structurally
different form. A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.
"""

from transitions_table import STATE_TRANSITIONS


class Hysteresis:
    def __init__(self, high: float, low: float):
        if low >= high:
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = "NORMAL"

    def update(self, value: float) -> str:
        transition = STATE_TRANSITIONS.get(self.state)
        if transition is not None:
            next_state = transition(self, value)
            if next_state is not None:
                self.state = next_state
        return self.state
