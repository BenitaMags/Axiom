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
        needed = int(full) + (1 if remainder > 0 else 0)

        return needed if needed < self.n else self.n

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        wanted = self.required_count(demand)

        ranking = []
        for idx in range(self.n):
            ranking.append((runtimes[idx], idx))
        ranking.sort()

        active = set()
        for pos in range(wanted):
            active.add(ranking[pos][1])

        plan = {}
        for idx in range(self.n):
            plan[idx] = idx in active
        return plan
