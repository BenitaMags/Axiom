"""transitions_table -- state transition rules for hysteresis detection.

Each rule is a (state, condition, next_state) triple. `condition` receives
(value, high, low) and decides whether the transition fires.
"""


def _trips_high(value, high, low):
    return value >= high


def _falls_low(value, high, low):
    return value <= low


_RULES = {
    "NORMAL": ("ALARM", _trips_high),
    "ALARM": ("NORMAL", _falls_low),
}


def next_state(state: str, value: float, high: float, low: float) -> str:
    rule = _RULES.get(state)
    if rule is None:
        return state
    candidate, condition = rule
    if condition(value, high, low):
        return candidate
    return state
