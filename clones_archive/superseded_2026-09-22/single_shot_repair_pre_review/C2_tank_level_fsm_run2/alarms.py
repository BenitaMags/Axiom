# CHOSEN-TARGET: python-statemachine
"""alarms -- level alarm evaluation and interlocks (first-party logic)."""

LOLO = 0.2
LOW = 1.0
HIGH = 8.0
HIHI = 9.5


def evaluate(level: float) -> set:
    """Return the set of active alarm labels for a tank level."""
    active = set()
    if level >= HIHI:
        active.add("HIHI")
    if level >= HIGH:
        active.add("HIGH")
    if level <= LOLO:
        active.add("LOLO")
    if level <= LOW:
        active.add("LOW")
    return active


def interlock_ok(level: float) -> bool:
    """Filling is permitted only below the HIHI trip point."""
    return level < HIHI
