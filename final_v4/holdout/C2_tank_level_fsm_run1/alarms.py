"""alarms -- level alarm evaluation and interlocks (first-party logic)."""

from alarm_thresholds import Thresholds

LOLO = 0.2
LOW = 1.0
HIGH = 8.0
HIHI = 9.5

_THRESHOLDS = Thresholds(lolo=LOLO, low=LOW, high=HIGH, hihi=HIHI)


def evaluate(level: float) -> set:
    """Return the set of active alarm labels for a tank level."""
    return _THRESHOLDS.active_labels(level)


def interlock_ok(level: float) -> bool:
    """Filling is permitted only below the HIHI trip point."""
    return _THRESHOLDS.interlock_ok(level)
