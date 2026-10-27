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
        scaled = raw_counts * self.scale

        self.history.append(scaled)
        excess = len(self.history) - self.window
        while excess > 0:
            del self.history[0]
            excess -= 1

        total = 0.0
        for entry in self.history:
            total += entry
        averaged = total / len(self.history)

        if self.previous_value is None:
            delta = 0.0
        else:
            diff = scaled - self.previous_value
            delta = diff if diff >= 0 else -diff

        self.previous_value = scaled

        is_anomalous = delta > self.roc_limit

        return {"value": scaled, "smoothed": averaged, "anomaly": is_anomalous}
