"""scheduler -- first-party lead-lag pump staging with runtime balancing.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. Given a flow demand and per-pump accumulated runtimes,
it decides how many pumps to stage and rotates duty to the least-run pumps.
"""
from functools import reduce


class PumpScheduler:
    def __init__(self, n_pumps: int = 3, pump_capacity: float = 50.0):
        self.n = n_pumps
        self.capacity = pump_capacity

    def required_count(self, demand: float) -> int:
        """Number of pumps needed to meet *demand* (capacity-staged)."""
        if demand <= 0:
            return 0

        needed = 0
        covered = 0.0
        while covered < demand and needed < self.n:
            covered += self.capacity
            needed += 1
        return needed

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        count_needed = self.required_count(demand)

        indices = list(range(self.n))
        ranked = sorted(indices, key=lambda idx: (runtimes[idx], idx))

        active = set()
        for pos, pump_idx in enumerate(ranked):
            if pos < count_needed:
                active.add(pump_idx)

        result = {}
        for pump_idx in range(self.n):
            result[pump_idx] = pump_idx in active
        return result
