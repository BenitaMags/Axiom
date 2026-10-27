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
        if count * self.capacity < demand:
            count += 1
        return count if count < self.n else self.n

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        k = self.required_count(demand)

        candidates = [(runtimes[i], i) for i in range(self.n)]
        candidates.sort()
        leading = {idx for _, idx in candidates[:k]}

        plan = {}
        for i in range(self.n):
            plan[i] = i in leading
        return plan
