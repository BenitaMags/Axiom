"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""

from smoothing import RollingWindow


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.roc_limit = roc_limit
        self._window_tracker = RollingWindow(window)
        self.last = None

    @property
    def window(self) -> int:
        return self._window_tracker.size

    @window.setter
    def window(self, value: int) -> None:
        self._window_tracker.size = value

    @property
    def buffer(self) -> list:
        return self._window_tracker.values

    @buffer.setter
    def buffer(self, value: list) -> None:
        self._window_tracker.values = value

    def _to_engineering_units(self, raw_counts: float) -> float:
        return raw_counts * self.scale

    def _is_anomalous(self, value: float) -> bool:
        if self.last is None:
            change = 0.0
        else:
            change = abs(value - self.last)
        return change > self.roc_limit

    def update(self, raw_counts: float) -> dict:
        value = self._to_engineering_units(raw_counts)
        smoothed = self._window_tracker.push_and_average(value)
        anomaly = self._is_anomalous(value)
        self.last = value
        return {"value": value, "smoothed": smoothed, "anomaly": anomaly}
