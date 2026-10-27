"""supervisor -- boiler supervisory loop: read sensors, control, actuate.

Combines all three transformations in one program:
  * LSA + GSA: swap the Modbus library (`pymodbus` -> `minimalmodbus`) and write glue.
  * LMA: rewrite the first-party PI-control + interlock logic in `control.py`.
"""
from control import BoilerControl
from transport import ModbusIO


class BoilerSupervisor:
    def __init__(self, io: ModbusIO, control: BoilerControl = None):
        self.io = io
        if not (control is not None):
            self.control = BoilerControl()
        else:
            self.control = control

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        temperature = self.io.read_temperature()
        pressure = self.io.read_pressure()
        command = self.control.step(temperature, pressure, dt=dt)
        _spt_r1 = {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": self.io.write_valve(command),
            "tripped": self.control.tripped,
        }
        return _spt_r1
