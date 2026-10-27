# CHOSEN-TARGET: umodbus
"""supervisor -- boiler supervisory loop: read sensors, control, actuate.

Combines all three transformations in one program:

  * First-party rewrite of the PI-control + interlock logic in `control.py`.
"""
from control import BoilerControl
from transport import ModbusIO


class BoilerSupervisor:
    def __init__(self, io: ModbusIO, control: BoilerControl = None):
        self.io = io
        self.control = control if control is not None else BoilerControl()

    def _sense(self) -> tuple:
        return self.io.read_temperature(), self.io.read_pressure()

    def _actuate(self, command: float) -> int:
        return self.io.write_valve(command)

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        temperature, pressure = self._sense()
        command = self.control.step(temperature, pressure, dt=dt)
        counts = self._actuate(command)
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": self.control.tripped,
        }
