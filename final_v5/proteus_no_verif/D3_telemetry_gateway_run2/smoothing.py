"""smoothing -- helper for maintaining a bounded moving-average window."""


class RollingWindow:
    """Keeps the most recent `capacity` values and computes their average."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.values = []

    def push(self, value: float) -> float:
        self.values.append(value)
        if len(self.values) > self.capacity:
            self.values.pop(0)
        return sum(self.values) / len(self.values)
