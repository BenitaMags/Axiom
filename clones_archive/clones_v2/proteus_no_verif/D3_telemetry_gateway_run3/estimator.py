"""estimator -- first-party engineering-unit estimation with anomaly flagging.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. It scales raw counts to engineering units, smooths with a
moving average, and flags a rate-of-change anomaly when consecutive samples jump too far.
"""


class Estimator:
    def __init__(self, scale: float = 0.1, window: int = 5, roc_limit: float = 5.0):
        self.scale = scale
        self.window = window
        self.roc_limit = roc_limit
        self.buffer = []
        self.last = None

    def update(self, raw_counts: float) -> dict:
        value = raw_counts * self.scale
        self.buffer.append(value)
        if len(self.buffer) > self.window:
            self.buffer.pop(0)
        smoothed = sum(self.buffer) / len(self.buffer)
        if not (self.last is None):
            _spt_t1 = value - self.last
            roc = abs(_spt_t1)
        else:
            roc = 0.0
        self.last = value
        _spt_r1 = {"value": value, "smoothed": smoothed, "anomaly": roc > self.roc_limit}
        return _spt_r1
