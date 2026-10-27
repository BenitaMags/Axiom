"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

This is the detection logic the LMA must rewrite into an equivalent but structurally
different form. A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.
"""

NORMAL_STATE = "NORMAL"
ALARM_STATE = "ALARM"


class Hysteresis:
    def __init__(self, high: float, low: float):
        if not (low < high):
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = NORMAL_STATE

    def update(self, value: float) -> str:
        if self.state == NORMAL_STATE:
            if value >= self.high:
                self.state = ALARM_STATE
        else:
            if value <= self.low:
                self.state = NORMAL_STATE
        return self.state
