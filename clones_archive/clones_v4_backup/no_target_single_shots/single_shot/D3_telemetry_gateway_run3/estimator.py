"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""
from collections import deque


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.window = window
        self.roc_limit = roc_limit
        self._history = deque()
        self.last = None

    @property
    def buffer(self):
        return list(self._history)

    def update(self, raw_counts: float) -> dict:
        engineering_value = raw_counts * self.scale

        self._history.append(engineering_value)
        while len(self._history) > self.window:
            self._history.popleft()

        total = 0.0
        count = 0
        for sample in self._history:
            total += sample
            count += 1
        moving_average = total / count

        if self.last is None:
            delta = 0.0
        else:
            delta = engineering_value - self.last
            if delta < 0:
                delta = -delta

        is_anomalous = delta > self.roc_limit
        self.last = engineering_value

        return {
            "value": engineering_value,
            "smoothed": moving_average,
            "anomaly": is_anomalous,
        }
