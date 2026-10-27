"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""

from smoothing import MovingWindow


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.roc_limit = roc_limit
        self._window_tracker = MovingWindow(window)
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

    def _rate_of_change(self, value: float) -> float:
        return 0.0 if self.last is None else abs(value - self.last)

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        smoothed = self._window_tracker.push(value)
        roc = self._rate_of_change(value)
        self.last = value
        return {"value": value, "smoothed": smoothed, "anomaly": roc > self.roc_limit}
