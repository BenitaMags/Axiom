"""alarm_thresholds -- value object carrying tank alarm setpoints."""

from dataclasses import dataclass


@dataclass
class Thresholds:
    lolo: float
    low: float
    high: float
    hihi: float

    def active_labels(self, level: float) -> set:
        checks = (
            ("HIHI", lambda v: v >= self.hihi),
            ("HIGH", lambda v: v >= self.high),
            ("LOLO", lambda v: v <= self.lolo),
            ("LOW", lambda v: v <= self.low),
        )
        return {label for label, predicate in checks if predicate(level)}

    def interlock_ok(self, level: float) -> bool:
        return level < self.hihi
