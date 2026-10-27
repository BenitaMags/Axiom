"""estimator -- first-party engineering-unit estimation with anomaly flagging.

Scales raw counts to engineering units, smooths with a moving average, and
flags a rate-of-change anomaly when consecutive samples jump too far.
"""
from collections import deque


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.window = window
        self.roc_limit = roc_limit
        self._history = deque()
        self._previous_value = None

    def update(self, raw_counts: float) -> dict:
        converted = raw_counts * self.scale

        self._history.append(converted)
        while len(self._history) > self.window:
            self._history.popleft()

        average = sum(self._history) / len(self._history)

        if self._previous_value is None:
            delta = 0.0
        else:
            delta = abs(converted - self._previous_value)
        self._previous_value = converted

        is_anomalous = delta > self.roc_limit

        return {
            "value": converted,
            "smoothed": average,
            "anomaly": is_anomalous,
        }

    @property
    def buffer(self):
        return list(self._history)

    @property
    def last(self):
        return self._previous_value
