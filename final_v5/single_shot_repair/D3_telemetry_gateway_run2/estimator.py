"""estimator -- first-party engineering-unit estimation with anomaly flagging.

Restructured (but functionally equivalent) implementation: instead of a plain
list-based moving-window buffer with manual trimming, this version tracks a
running sum alongside a deque-like ring buffer, and separates the "scale to
engineering units" and "rate-of-change anomaly check" concerns into small
helper methods.
"""
from collections import deque


class _RollingWindow:
    """Fixed-capacity rolling window that maintains a running sum."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._values = deque()
        self._sum = 0.0

    def push(self, value: float) -> None:
        self._values.append(value)
        self._sum += value
        if len(self._values) > self.capacity:
            removed = self._values.popleft()
            self._sum -= removed

    def mean(self) -> float:
        if not self._values:
            return 0.0
        return self._sum / len(self._values)

    def __len__(self):
        return len(self._values)


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.window = window
        self.roc_limit = roc_limit
        self._rolling = _RollingWindow(window)
        self.last = None

    @property
    def buffer(self):
        # Preserve the previous public attribute shape (a list) for
        # compatibility with any external inspection of buffer contents.
        return list(self._rolling._values)

    def _scale_value(self, raw_counts: float) -> float:
        return raw_counts * self.scale

    def _check_anomaly(self, value: float) -> bool:
        if self.last is None:
            roc = 0.0
        else:
            roc = abs(value - self.last)
        self.last = value
        return roc > self.roc_limit

    def update(self, raw_counts: float) -> dict:
        value = self._scale_value(raw_counts)
        anomaly = self._check_anomaly(value)
        self._rolling.push(value)
        smoothed = self._rolling.mean()
        return {"value": value, "smoothed": smoothed, "anomaly": anomaly}
