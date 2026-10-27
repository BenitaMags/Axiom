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
        sensed_temp = self.io.read_temperature()
        sensed_pressure = self.io.read_pressure()
        valve_command = self.control.step(sensed_temp, sensed_pressure, dt=dt)
        actuator_counts = self.io.write_valve(valve_command)
        record = {
            "temperature": sensed_temp,
            "pressure": sensed_pressure,
            "valve_pct": valve_command,
            "valve_counts": actuator_counts,
            "tripped": self.control.tripped,
        }
        return record
