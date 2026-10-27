"""cycle_record -- value object bundling one supervisory cycle's results."""
from dataclasses import dataclass, asdict


@dataclass
class CycleRecord:
    temperature: float
    pressure: float
    valve_pct: float
    valve_counts: int
    tripped: bool

    def as_dict(self) -> dict:
        return asdict(self)
