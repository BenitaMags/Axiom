"""hysteresis -- dual-threshold hysteresis detector (first-party core logic).

This is the detection logic the LMA must rewrite into an equivalent but structurally
different form. A value at or above `high` trips ALARM; the detector only clears once
the value falls back to or below `low`, preventing chatter within the deadband.
"""

NORMAL = "NORMAL"
ALARM = "ALARM"


def _normal_transition(value: float, high: float, low: float) -> str:
    return ALARM if value >= high else NORMAL


def _alarm_transition(value: float, high: float, low: float) -> str:
    return NORMAL if value <= low else ALARM


_TRANSITIONS = {
    NORMAL: _normal_transition,
    ALARM: _alarm_transition,
}


class Hysteresis:
    def __init__(self, high: float, low: float):
        if low >= high:
            raise ValueError("low must be < high")
        self.high = high
        self.low = low
        self.state = NORMAL

    def update(self, value: float) -> str:
        transition = _TRANSITIONS[self.state]
        self.state = transition(value, self.high, self.low)
        return self.state
