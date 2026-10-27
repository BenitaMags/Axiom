"""sample_window -- helper managing a bounded FIFO buffer with averaging."""


class SampleWindow:
    """A simple bounded FIFO buffer of numeric samples."""

    def __init__(self, size: int):
        self.size = size
        self.values = []

    def push_and_average(self, value: float) -> float:
        self.values.append(value)
        if len(self.values) > self.size:
            self.values.pop(0)
        return sum(self.values) / len(self.values)
