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

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        stages = (
            self._read_sensors,
            self._compute_command,
            self._actuate,
        )
        ctx = {"dt": dt}
        for stage in stages:
            stage(ctx)
        record = CycleRecord(
            temperature=ctx["temperature"],
            pressure=ctx["pressure"],
            valve_pct=ctx["command"],
            valve_counts=ctx["counts"],
            tripped=self.control.tripped,
        )
        return record.as_dict()

    def _read_sensors(self, ctx: dict) -> None:
        ctx["temperature"] = self.io.read_temperature()
        ctx["pressure"] = self.io.read_pressure()

    def _compute_command(self, ctx: dict) -> None:
        ctx["command"] = self.control.step(
            ctx["temperature"], ctx["pressure"], dt=ctx["dt"]
        )

    def _actuate(self, ctx: dict) -> None:
        ctx["counts"] = self.io.write_valve(ctx["command"])
