"""valve_flow -- helpers for computing net flow through inlet/outlet valves."""


def compute_level_delta(inlet: bool, outlet: bool, fill_rate: float, drain_rate: float) -> float:
    """Compute the net per-unit-time change in level from valve states."""
    delta = 0.0
    if inlet:
        delta += fill_rate
    if outlet:
        delta -= drain_rate
    return delta
