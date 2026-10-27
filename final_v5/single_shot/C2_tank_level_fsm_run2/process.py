"""process -- physical process variables and a simple update model."""
from dataclasses import dataclass


@dataclass
class Process:
    level: float = 0.0
    inlet: bool = False
    outlet: bool = False

    def apply(self, dt: float = 1.0, fill_rate: float = 0.5, drain_rate: float = 0.4) -> float:
        """Advance the level given the current valve commands."""
        delta = 0.0
        if self.inlet:
            delta += fill_rate
        if self.outlet:
            delta -= drain_rate
        self.level = max(0.0, self.level + delta * dt)
        return self.level
