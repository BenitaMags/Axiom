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
        full, remainder = divmod(demand, self.capacity)
        needed = int(full) + (1 if remainder > 1e-12 else 0)
        return needed if needed < self.n else self.n

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        count_needed = self.required_count(demand)

        ranked_indices = []
        for idx in range(self.n):
            ranked_indices.append((runtimes[idx], idx))
        ranked_indices.sort()

        active = set()
        for position, (_, pump_idx) in enumerate(ranked_indices):
            if position < count_needed:
                active.add(pump_idx)

        plan = {}
        for pump_idx in range(self.n):
            plan[pump_idx] = pump_idx in active
        return plan
