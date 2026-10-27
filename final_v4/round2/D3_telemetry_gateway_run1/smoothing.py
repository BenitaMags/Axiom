"""smoothing -- helper for maintaining a bounded moving-average window."""


class MovingAverage:
    def __init__(self, window: int):
        self.window = window
        self.samples = []

    def push(self, value: float) -> float:
        self.samples.append(value)
        if len(self.samples) > self.window:
            self.samples.pop(0)
        return sum(self.samples) / len(self.samples)
