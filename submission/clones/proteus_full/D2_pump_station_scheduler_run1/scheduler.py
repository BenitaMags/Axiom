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
        _spt_t1 = demand / self.capacity
        _t0 = math.ceil(_spt_t1)
        _spt_r1 = min(self.n, _t0)
        return _spt_r1

    def select(self, demand: float, runtimes) -> dict:
        """Return {pump_index: on?} staging the least-run pumps first (lead-lag)."""
        k = self.required_count(demand)
        _t1 = range(self.n)
        _t2 = lambda i: (runtimes[i], i)
        # Rotate duty to balance wear: prefer pumps with the least accumulated runtime.
        order = sorted(_t1, key=_t2)
        _t3 = order[:k]
        on = set(_t3)
        _spt_r2 = {i: (i in on) for i in range(self.n)}
        return _spt_r2
