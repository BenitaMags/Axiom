"""Table-driven state transition logic for hysteresis detectors."""

STATE_NORMAL = "NORMAL"
STATE_ALARM = "ALARM"


def _check_normal(value: float, high: float, low: float) -> str:
    return STATE_ALARM if value >= high else STATE_NORMAL


def _check_alarm(value: float, high: float, low: float) -> str:
    return STATE_NORMAL if value <= low else STATE_ALARM


_TRANSITIONS = {
    STATE_NORMAL: _check_normal,
    STATE_ALARM: _check_alarm,
}


def next_state(state: str, value: float, high: float, low: float) -> str:
    handler = _TRANSITIONS.get(state)
    if handler is None:
        return state
    return handler(value, high, low)
