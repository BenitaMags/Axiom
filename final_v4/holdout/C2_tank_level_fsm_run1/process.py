"""process -- physical process variables and a simple update model."""
from valve_flow import compute_level_delta


class Process:
    def __init__(self, level: float = 0.0, inlet: bool = False, outlet: bool = False):
        self.level = level
        self.inlet = inlet
        self.outlet = outlet

    def __repr__(self) -> str:
        return f"Process(level={self.level!r}, inlet={self.inlet!r}, outlet={self.outlet!r})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Process):
            return NotImplemented
        return (self.level, self.inlet, self.outlet) == (other.level, other.inlet, other.outlet)

    def apply(self, dt: float = 1.0, fill_rate: float = 0.5, drain_rate: float = 0.4) -> float:
        """Advance the level given the current valve commands."""
        delta = compute_level_delta(self.inlet, self.outlet, fill_rate, drain_rate)
        self.level = max(0.0, self.level + delta * dt)
        return self.level
