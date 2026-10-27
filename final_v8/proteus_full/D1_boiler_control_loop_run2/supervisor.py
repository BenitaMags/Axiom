"""supervisor -- boiler supervisory loop: read sensors, control, actuate.

Combines all three transformations in one program:
  * LSA + GSA: swap the Modbus library (`pymodbus` -> `minimalmodbus`) and write glue.
  * LMA: rewrite the first-party PI-control + interlock logic in `control.py`.
"""
from control import BoilerControl
from transport import ModbusIO
from cycle_record import CycleRecord


class BoilerSupervisor:
    def __init__(self, io: ModbusIO, control: BoilerControl = None):
        self.io = io
        self.control = control if control is not None else BoilerControl()

    def _read_inputs(self):
        return self.io.read_temperature(), self.io.read_pressure()

    def _actuate(self, command):
        return self.io.write_valve(command)

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        temperature, pressure = self._read_inputs()
        command = self.control.step(temperature, pressure, dt=dt)
        counts = self._actuate(command)
        record = CycleRecord(
            temperature=temperature,
            pressure=pressure,
            valve_pct=command,
            valve_counts=counts,
            tripped=self.control.tripped,
        )
        return record.as_dict()
