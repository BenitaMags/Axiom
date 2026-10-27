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
            delta = delta + fill_rate
        if self.outlet:
            delta = delta - drain_rate
        self.level = max(0.0, self.level + delta * dt)
        _spt_r1 = self.level
        return _spt_r1
