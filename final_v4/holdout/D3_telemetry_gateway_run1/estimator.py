"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""

from smoothing import MovingWindow


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self._scale = scale
        self._roc_limit = roc_limit
        self._window_state = MovingWindow(window)
        self._last = None

    @property
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        self._scale = value

    @property
    def window(self):
        return self._window_state.capacity

    @window.setter
    def window(self, value):
        self._window_state.capacity = value

    @property
    def roc_limit(self):
        return self._roc_limit

    @roc_limit.setter
    def roc_limit(self, value):
        self._roc_limit = value

    @property
    def buffer(self):
        return self._window_state.values

    @buffer.setter
    def buffer(self, value):
        self._window_state.values = value

    @property
    def last(self):
        return self._last

    @last.setter
    def last(self, value):
        self._last = value

    def _rate_of_change(self, value: float) -> float:
        if self._last is None:
            return 0.0
        return abs(value - self._last)

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self._scale
        smoothed = self._window_state.push_and_average(value)
        roc = self._rate_of_change(value)
        self._last = value
        return {
            "value": value,
            "smoothed": smoothed,
            "anomaly": roc > self._roc_limit,
        }
