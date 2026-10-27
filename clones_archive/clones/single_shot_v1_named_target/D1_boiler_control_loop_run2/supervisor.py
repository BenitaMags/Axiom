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
        self.control = control if control is not None else BoilerControl()

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        current_temp = self.io.read_temperature()
        current_pressure = self.io.read_pressure()

        valve_command = self.control.step(current_temp, current_pressure, dt=dt)
        raw_counts = self.io.write_valve(valve_command)

        record = {}
        record["temperature"] = current_temp
        record["pressure"] = current_pressure
        record["valve_pct"] = valve_command
        record["valve_counts"] = raw_counts
        record["tripped"] = self.control.tripped
        return record
