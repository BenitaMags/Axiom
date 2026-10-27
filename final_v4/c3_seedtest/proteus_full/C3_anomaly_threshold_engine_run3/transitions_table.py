"""Dispatch table of hysteresis state transition rules."""

STATE_NORMAL = "NORMAL"
STATE_ALARM = "ALARM"


def _from_normal(value: float, high: float, low: float) -> str:
    if value >= high:
        return STATE_ALARM
    return STATE_NORMAL


def _from_alarm(value: float, high: float, low: float) -> str:
    if value <= low:
        return STATE_NORMAL
    return STATE_ALARM


_TRANSITIONS = {
    STATE_NORMAL: _from_normal,
    STATE_ALARM: _from_alarm,
}


def next_state(state: str, value: float, high: float, low: float) -> str:
    handler = _TRANSITIONS.get(state)
    if handler is None:
        return state
    return handler(value, high, low)
