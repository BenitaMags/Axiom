"""cycle_pipeline -- stage-based execution of one boiler supervisory cycle.

Decomposes a supervisory cycle into three discrete stages (read sensors,
compute a control command, actuate the valve) and bundles the result of a
cycle into a small value object rather than an ad-hoc dict built inline.
"""
from dataclasses import dataclass


@dataclass
class CycleRecord:
    """The outcome of a single supervisory cycle."""

    temperature: float
    pressure: float
    valve_pct: float
    valve_counts: int
    tripped: bool

    def as_dict(self) -> dict:
        return {
            "temperature": self.temperature,
            "pressure": self.pressure,
            "valve_pct": self.valve_pct,
            "valve_counts": self.valve_counts,
            "tripped": self.tripped,
        }


class SupervisoryPipeline:
    """Runs the read -> control -> actuate stages in sequence."""

    def __init__(self, io, control):
        self.io = io
        self.control = control

    def _read_sensors(self):
        temperature = self.io.read_temperature()
        pressure = self.io.read_pressure()
        return temperature, pressure

    def _compute_command(self, temperature, pressure, dt):
        return self.control.step(temperature, pressure, dt=dt)

    def _actuate(self, command):
        return self.io.write_valve(command)

    def run(self, dt: float) -> CycleRecord:
        temperature, pressure = self._read_sensors()
        command = self._compute_command(temperature, pressure, dt)
        counts = self._actuate(command)
        return CycleRecord(
            temperature=temperature,
            pressure=pressure,
            valve_pct=command,
            valve_counts=counts,
            tripped=self.control.tripped,
        )
