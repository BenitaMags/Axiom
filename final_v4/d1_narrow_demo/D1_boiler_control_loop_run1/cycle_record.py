"""Value object carrying the result of a single boiler supervisory cycle."""
from dataclasses import dataclass


@dataclass
class CycleRecord:
    temperature: float
    pressure: float
    valve_pct: float
    valve_counts: int
    tripped: bool

    def to_dict(self) -> dict:
        """Return the plain-dict representation used by public APIs."""
        return {
            "temperature": self.temperature,
            "pressure": self.pressure,
            "valve_pct": self.valve_pct,
            "valve_counts": self.valve_counts,
            "tripped": self.tripped,
        }
