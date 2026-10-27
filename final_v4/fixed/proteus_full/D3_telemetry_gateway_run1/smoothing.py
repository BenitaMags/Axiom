"""smoothing -- helper value object for maintaining a bounded moving window."""


class MovingWindow:
    """Maintains a list of values capped at a given size and computes averages."""

    def __init__(self, size: int):
        self.size = size
        self.values = []

    def push(self, value: float) -> float:
        self.values.append(value)
        if len(self.values) > self.size:
            self.values.pop(0)
        return sum(self.values) / len(self.values)
