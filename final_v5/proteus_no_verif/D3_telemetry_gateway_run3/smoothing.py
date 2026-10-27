"""smoothing -- helper value object for maintaining a bounded moving window."""


class MovingWindow:
    """Tracks a bounded list of values and computes their running average."""

    def __init__(self, size: int):
        self.size = size
        self.values = []

    def push(self, value: float) -> float:
        self.values.append(value)
        if len(self.values) > self.size:
            self.values.pop(0)
        return sum(self.values) / len(self.values)
