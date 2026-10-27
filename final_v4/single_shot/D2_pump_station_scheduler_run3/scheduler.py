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

        ranked = []
        for idx in range(self.n):
            ranked.append((runtimes[idx], idx))
        ranked.sort()

        active = set()
        for _, idx in ranked[:k]:
            active.add(idx)

        plan = {}
        for idx in range(self.n):
            plan[idx] = idx in active
        return plan
