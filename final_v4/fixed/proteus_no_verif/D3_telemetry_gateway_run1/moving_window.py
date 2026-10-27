"""moving_window -- a small helper encapsulating a bounded FIFO buffer.

Extracted so estimator.py can delegate the buffering/averaging concern.
"""


class MovingWindow:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.items = []

    def push(self, value: float) -> None:
        self.items.append(value)
        if len(self.items) > self.capacity:
            self.items.pop(0)

    def average(self) -> float:
        return sum(self.items) / len(self.items)
