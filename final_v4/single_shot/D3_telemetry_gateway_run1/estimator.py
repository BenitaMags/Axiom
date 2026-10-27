"""estimator -- first-party engineering-unit estimation with anomaly flagging.

Scales raw counts to engineering units, smooths with a moving average, and
flags a rate-of-change anomaly when consecutive samples jump too far.

Restructured internally to separate the scaling/smoothing pipeline from the
anomaly detection step, while preserving the exact public API and behaviour.
"""


class _MovingAverage:
    """Fixed-size sliding-window moving average accumulator."""

    def __init__(self, window: int):
        self.window = window
        self._values = []

    def push(self, value: float) -> float:
        self._values.append(value)
        if len(self._values) > self.window:
            self._values.pop(0)
        return sum(self._values) / len(self._values)


class _RateOfChangeDetector:
    """Flags whether the jump between consecutive samples exceeds a limit."""

    def __init__(self, limit: float):
        self.limit = limit
        self._previous = None

    def check(self, value: float) -> bool:
        if self._previous is None:
            change = 0.0
        else:
            change = abs(value - self._previous)
        self._previous = value
        return change > self.limit


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.window = window
        self.roc_limit = roc_limit
        self._averager = _MovingAverage(window)
        self._roc = _RateOfChangeDetector(roc_limit)

    @property
    def buffer(self):
        return self._averager._values

    @property
    def last(self):
        return self._roc._previous

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        smoothed = self._averager.push(value)
        anomaly = self._roc.check(value)
        return {"value": value, "smoothed": smoothed, "anomaly": anomaly}
