"""Helper routines for lead-lag pump staging decisions."""
import math


def compute_required_count(demand: float, capacity: float, n_pumps: int) -> int:
    """Number of pumps (capped at *n_pumps*) needed to cover *demand*."""
    if demand <= 0:
        return 0
    return min(n_pumps, math.ceil(demand / capacity))


def rank_pumps_by_wear(n_pumps: int, runtimes) -> list:
    """Return pump indices ordered by ascending accumulated runtime, then index."""
    return sorted(range(n_pumps), key=lambda i: (runtimes[i], i))
