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
        self.control = control or BoilerControl()

    def _gather_process_values(self):
        temp = self.io.read_temperature()
        pres = self.io.read_pressure()
        return temp, pres

    def _actuate(self, command: float):
        return self.io.write_valve(command)

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        temperature, pressure = self._gather_process_values()
        command = self.control.step(temperature, pressure, dt=dt)
        counts = self._actuate(command)

        record = {}
        record["temperature"] = temperature
        record["pressure"] = pressure
        record["valve_pct"] = command
        record["valve_counts"] = counts
        record["tripped"] = self.control.tripped
        return record
