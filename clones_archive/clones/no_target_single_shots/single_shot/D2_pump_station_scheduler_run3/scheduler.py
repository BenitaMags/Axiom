"""scheduler -- first-party lead-lag pump staging with runtime balancing.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form. Given a flow demand and per-pump accumulated runtimes,
it decides how many pumps to stage and rotates duty to the least-run pumps.
"""


class PumpScheduler:
    def __init__(self, n_pumps: int = 3, pump_capacity: float = 50.0):
        self.n = n_pumps
        self.capacity = pump_capacity

    def required_count(self, demand: float) -> int:
        """Number of pumps needed to meet *demand* (capacity-staged)."""
        if demand <= 0:
            return 0

        count = int(demand // self.capacity)
        remainder = demand - (count * self.capacity)
        if remainder > 0:
            count += 1

        return count if count < self.n else self.n

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        needed = self.required_count(demand)

        candidates = list(range(self.n))
        candidates.sort(key=lambda idx: (runtimes[idx], idx))

        active = set()
        for pos, idx in enumerate(candidates):
            if pos >= needed:
                break
            active.add(idx)

        plan = {}
        for pump_idx in range(self.n):
            plan[pump_idx] = pump_idx in active
        return plan
