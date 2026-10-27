"""transitions_table -- dispatch table describing hysteresis state transitions.

Each entry maps a current state to a tuple of:
    (threshold attribute name, comparison function, next state)

The comparison function receives (value, threshold) and returns True when the
transition should occur.
"""

import operator

TRANSITIONS = {
    "NORMAL": ("high", operator.ge, "ALARM"),
    "ALARM": ("low", operator.le, "NORMAL"),
}
