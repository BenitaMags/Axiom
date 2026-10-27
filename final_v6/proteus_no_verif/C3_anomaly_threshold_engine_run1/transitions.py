"""Helper module for state-transition rules used by the hysteresis detector."""

STATE_NORMAL = "NORMAL"
STATE_ALARM = "ALARM"


def _trip(value: float, high: float, low: float) -> str:
    return STATE_ALARM if value >= high else STATE_NORMAL


def _clear(value: float, high: float, low: float) -> str:
    return STATE_NORMAL if value <= low else STATE_ALARM


_RULES = {
    STATE_NORMAL: _trip,
    STATE_ALARM: _clear,
}


def next_state(current: str, value: float, high: float, low: float) -> str:
    rule = _RULES.get(current)
    if rule is None:
        return current
    return rule(value, high, low)
