"""estimator -- first-party engineering-unit estimation with anomaly flagging.

Restructured internally (moving-average state is tracked incrementally via a running
sum plus a deque rather than recomputed with sum() each call, and the anomaly/rate-of-
change check is delegated to a small helper), while keeping the exact same public API,
numeric constants, and behaviour as before.
"""
from collections import deque


class _RateOfChangeMonitor:
    """Tracks the previous value and flags when the jump exceeds a limit."""

    def __init__(self, limit: float):
        self.limit = limit
        self._previous = None

    def check(self, value: float) -> bool:
        if self._previous is None:
            flagged = False
        else:
            flagged = abs(value - self._previous) > self.limit
        self._previous = value
        return flagged


class _MovingAverage:
    """Maintains a running average over a bounded window without re-summing."""

    def __init__(self, window: int):
        self.window = window
        self._values = deque()
        self._total = 0.0

    def add(self, value: float) -> float:
        self._values.append(value)
        self._total += value
        if len(self._values) > self.window:
            removed = self._values.popleft()
            self._total -= removed
        return self._total / len(self._values)


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.window = window
        self.roc_limit = roc_limit
        self._averager = _MovingAverage(window)
        self._roc_monitor = _RateOfChangeMonitor(roc_limit)

    @property
    def buffer(self):
        return list(self._averager._values)

    @property
    def last(self):
        return self._roc_monitor._previous

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        anomaly = self._roc_monitor.check(value)
        smoothed = self._averager.add(value)
        return {"value": value, "smoothed": smoothed, "anomaly": anomaly}
