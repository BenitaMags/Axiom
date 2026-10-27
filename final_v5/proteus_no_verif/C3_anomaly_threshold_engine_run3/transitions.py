"""Helper transition table for hysteresis state evaluation."""

NORMAL_STATE = "NORMAL"
ALARM_STATE = "ALARM"


def _from_normal(value: float, high: float, low: float) -> str:
    if value >= high:
        return ALARM_STATE
    return NORMAL_STATE


def _from_alarm(value: float, high: float, low: float) -> str:
    if value <= low:
        return NORMAL_STATE
    return ALARM_STATE


_TRANSITIONS = {
    NORMAL_STATE: _from_normal,
    ALARM_STATE: _from_alarm,
}


def next_state(current_state: str, value: float, high: float, low: float) -> str:
    handler = _TRANSITIONS.get(current_state)
    if handler is None:
        return current_state
    return handler(value, high, low)
