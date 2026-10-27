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
        self._rolling = RollingWindow(window)
        self.last = None

    @property
    def window(self) -> int:
        return self._rolling.capacity

    @window.setter
    def window(self, value: int) -> None:
        self._rolling.capacity = value

    @property
    def buffer(self) -> list:
        return self._rolling.values

    @buffer.setter
    def buffer(self, value: list) -> None:
        self._rolling.values = value

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        smoothed = self._rolling.push(value)
        roc = 0.0 if self.last is None else abs(value - self.last)
        self.last = value
        return {"value": value, "smoothed": smoothed, "anomaly": roc > self.roc_limit}
