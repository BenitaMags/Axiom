"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""

from sample_window import SampleWindow


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.roc_limit = roc_limit
        self._window_state = SampleWindow(window)
        self.last = None

    @property
    def window(self) -> int:
        return self._window_state.size

    @window.setter
    def window(self, value: int) -> None:
        self._window_state.size = value

    @property
    def buffer(self) -> list:
        return self._window_state.values

    @buffer.setter
    def buffer(self, value: list) -> None:
        self._window_state.values = value

    def _rate_of_change(self, value: float) -> float:
        if self.last is None:
            return 0.0
        return abs(value - self.last)

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        smoothed = self._window_state.push_and_average(value)
        roc = self._rate_of_change(value)
        self.last = value
        return {"value": value, "smoothed": smoothed, "anomaly": roc > self.roc_limit}
