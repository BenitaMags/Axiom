"""supervisor -- boiler supervisory loop: read sensors, control, actuate.

Combines all three transformations in one program:

  * LMA: rewrite the first-party PI-control + interlock logic in `control.py`.
"""
from control import BoilerControl
from transport import ModbusIO


class BoilerSupervisor:
    def __init__(self, io: ModbusIO, control: BoilerControl = None):
        self.io = io
        self.control = control if control is not None else BoilerControl()

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        temperature = self.io.read_temperature()
        pressure = self.io.read_pressure()
        command = self.control.step(temperature, pressure, dt=dt)
        counts = self.io.write_valve(command)
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": self.control.tripped,
        }
