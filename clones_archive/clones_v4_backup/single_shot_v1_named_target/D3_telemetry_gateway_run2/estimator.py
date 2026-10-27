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
        self.history = []
        self.previous_value = None

    def update(self, raw_counts: float) -> dict:
        engineering_value = raw_counts * self.scale

        self.history.append(engineering_value)
        overflow = len(self.history) - self.window
        while overflow > 0:
            del self.history[0]
            overflow -= 1

        total = 0.0
        for sample in self.history:
            total += sample
        moving_average = total / len(self.history)

        if self.previous_value is None:
            delta = 0.0
        else:
            delta = engineering_value - self.previous_value
            if delta < 0:
                delta = -delta

        self.previous_value = engineering_value

        is_anomalous = delta > self.roc_limit

        return {
            "value": engineering_value,
            "smoothed": moving_average,
            "anomaly": is_anomalous,
        }
