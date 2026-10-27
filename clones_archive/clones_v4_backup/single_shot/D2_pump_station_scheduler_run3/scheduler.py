"""scheduler -- first-party lead-lag pump staging with runtime balancing.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. Given a flow demand and per-pump accumulated runtimes,
it decides how many pumps to stage and rotates duty to the least-run pumps.
"""
import math


class PumpScheduler:
    def __init__(self, n_pumps: int = 3, pump_capacity: float = 50.0):
        self.n = n_pumps
        self.capacity = pump_capacity

    def required_count(self, demand: float) -> int:
        """Number of pumps needed to meet *demand* (capacity-staged)."""
        if demand <= 0:
            return 0
        return min(self.n, math.ceil(demand / self.capacity))

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        k = self.required_count(demand)
        # Rotate duty to balance wear: prefer pumps with the least accumulated runtime.
        order = sorted(range(self.n), key=lambda i: (runtimes[i], i))
        on = set(order[:k])
        return {i: (i in on) for i in range(self.n)}
