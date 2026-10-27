"""Value object carrying the result of one supervisory cycle."""
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
