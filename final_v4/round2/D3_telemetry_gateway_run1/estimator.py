"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""

from smoothing import MovingAverage


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.roc_limit = roc_limit
        self._averager = MovingAverage(window)
        self.last = None

    @property
    def window(self) -> int:
        return self._averager.window

    @window.setter
    def window(self, value: int) -> None:
        self._averager.window = value

    @property
    def buffer(self) -> list:
        return self._averager.samples

    @buffer.setter
    def buffer(self, value: list) -> None:
        self._averager.samples = value

    def _is_anomaly(self, value: float) -> bool:
        if self.last is None:
            roc = 0.0
        else:
            roc = abs(value - self.last)
        return roc > self.roc_limit

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        anomaly = self._is_anomaly(value)
        smoothed = self._averager.push(value)
        self.last = value
        return {"value": value, "smoothed": smoothed, "anomaly": anomaly}
