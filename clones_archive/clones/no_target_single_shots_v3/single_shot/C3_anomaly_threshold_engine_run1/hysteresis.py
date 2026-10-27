"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

This is the detection logic the LMA must rewrite into an equivalent but structurally
different form. A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.
"""


class Hysteresis:
    def __init__(self, high: float, low: float):
        if low >= high:
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = "NORMAL"

    def update(self, value: float) -> str:
        if self.state == "NORMAL" and value >= self.high:
            self.state = "ALARM"
        elif self.state == "ALARM" and value <= self.low:
            self.state = "NORMAL"
        return self.state
