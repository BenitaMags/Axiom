"""Transition rules for hysteresis-style dual-threshold detectors.

Each rule describes, for a given current state, the condition under which the
detector transitions to a new state. The table is consulted in order; the
first matching rule wins. If no rule matches, the state is unchanged.
"""

from typing import Callable, NamedTuple

STATE_NORMAL = "NORMAL"
STATE_ALARM = "ALARM"


class TransitionRule(NamedTuple):
    from_state: str
    to_state: str
    condition: Callable[[float, float, float], bool]


_RULES = (
    TransitionRule(
        from_state=STATE_NORMAL,
        to_state=STATE_ALARM,
        condition=lambda value, high, low: value >= high,
    ),
    TransitionRule(
        from_state=STATE_ALARM,
        to_state=STATE_NORMAL,
        condition=lambda value, high, low: value <= low,
    ),
)


def next_state(current_state: str, value: float, high: float, low: float) -> str:
    for rule in _RULES:
        if rule.from_state == current_state and rule.condition(value, high, low):
            return rule.to_state
    return current_state
