"""Helper value object encapsulating pump-staging math for the scheduler."""
import math
from typing import NamedTuple


class PumpStagingPlan(NamedTuple):
    n_pumps: int
    pump_capacity: float

    def required_count(self, demand: float) -> int:
        if demand <= 0:
            return 0
        return min(self.n_pumps, math.ceil(demand / self.pump_capacity))

    def _rotation_order(self, runtimes):
        return sorted(range(self.n_pumps), key=lambda i: (runtimes[i], i))

    def select(self, demand: float, runtimes) -> dict:
        k = self.required_count(demand)
        order = self._rotation_order(runtimes)
        on = set(order[:k])
        return {i: (i in on) for i in range(self.n_pumps)}
