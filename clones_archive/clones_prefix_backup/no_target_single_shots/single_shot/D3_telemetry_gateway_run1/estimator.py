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
        current_value = raw_counts * self.scale

        history = self.buffer
        history.append(current_value)
        excess = len(history) - self.window
        while excess > 0:
            history.pop(0)
            excess -= 1

        total = 0.0
        for sample in history:
            total += sample
        smoothed_value = total / len(history)

        if self.last is None:
            rate_of_change = 0.0
        else:
            delta = current_value - self.last
            rate_of_change = delta if delta >= 0 else -delta

        self.last = current_value

        is_anomalous = rate_of_change > self.roc_limit

        return {
            "value": current_value,
            "smoothed": smoothed_value,
            "anomaly": is_anomalous,
        }
