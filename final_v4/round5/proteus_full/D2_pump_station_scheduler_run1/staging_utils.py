"""Helper routines for pump staging calculations."""
import math


def count_needed(demand: float, capacity: float, n: int) -> int:
    """Compute how many units (capped at *n*) are needed to cover *demand*."""
    if demand <= 0:
        return 0
    return min(n, math.ceil(demand / capacity))


def rank_pumps(n: int, runtimes) -> list:
    """Return pump indices 0..n-1 ordered by ascending runtime, ties by index."""
    return sorted(range(n), key=lambda i: (runtimes[i], i))
