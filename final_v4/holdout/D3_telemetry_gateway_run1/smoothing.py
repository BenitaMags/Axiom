"""smoothing -- helper value object for maintaining a bounded moving window."""


class MovingWindow:
    """Maintains a list of values capped at a given capacity and computes averages."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.values = []

    def push_and_average(self, value: float) -> float:
        self.values.append(value)
        if len(self.values) > self.capacity:
            self.values.pop(0)
        return sum(self.values) / len(self.values)
