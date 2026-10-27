"""Dispatch table describing hysteresis state transitions.

Each entry maps a current state to a (condition, next_state) pair. The
condition function receives (value, high, low) and returns True when the
transition should occur.
"""


def _should_trip(value, high, low):
    return value >= high


def _should_clear(value, high, low):
    return value <= low


TRANSITIONS = {
    "NORMAL": (_should_trip, "ALARM"),
    "ALARM": (_should_clear, "NORMAL"),
}
