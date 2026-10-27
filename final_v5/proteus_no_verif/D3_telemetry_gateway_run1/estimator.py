"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""

from moving_window import MovingWindow


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self._scale = scale
        self._roc_limit = roc_limit
        self._window_buffer = MovingWindow(window)
        self.last = None

    @property
    def scale(self) -> float:
        return self._scale

    @scale.setter
    def scale(self, value: float) -> None:
        self._scale = value

    @property
    def window(self) -> int:
        return self._window_buffer.capacity

    @window.setter
    def window(self, value: int) -> None:
        self._window_buffer.capacity = value

    @property
    def roc_limit(self) -> float:
        return self._roc_limit

    @roc_limit.setter
    def roc_limit(self, value: float) -> None:
        self._roc_limit = value

    @property
    def buffer(self) -> list:
        return self._window_buffer.items

    @buffer.setter
    def buffer(self, value: list) -> None:
        self._window_buffer.items = value

    def _rate_of_change(self, value: float) -> float:
        if self.last is None:
            return 0.0
        return abs(value - self.last)

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self._scale
        self._window_buffer.push(value)
        smoothed = self._window_buffer.average()
        roc = self._rate_of_change(value)
        self.last = value
        return {"value": value, "smoothed": smoothed, "anomaly": roc > self._roc_limit}
