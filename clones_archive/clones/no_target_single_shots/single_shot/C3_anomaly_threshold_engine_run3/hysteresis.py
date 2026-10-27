"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.
"""

_NORMAL = "NORMAL"
_ALARM = "ALARM"


class Hysteresis:
    def __init__(self, high: float, low: float):
        if low >= high:
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = _NORMAL

    def update(self, value: float) -> str:
        if self.state == _NORMAL:
            if value >= self.high:
                self.state = _ALARM
        else:
            if value <= self.low:
                self.state = _NORMAL
        return self.state
