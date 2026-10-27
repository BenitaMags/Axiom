"""supervisor -- boiler supervisory loop: sense, decide, actuate.

Clone of the reference supervisor.  One cycle is a pipeline of three stages that each
contribute their own fields to the cycle record, instead of a single method that
performs every I/O call inline and assembles the record at the end.
"""
from control import BoilerControl
from transport import ModbusIO


class BoilerSupervisor:
    def __init__(self, io: ModbusIO, control: BoilerControl = None):
        self.io = io
        self.control = control
        if control is None:
            self.control = BoilerControl()

    def cycle(self, dt: float = 1.0) -> dict:
        """Run one supervisory cycle and return the resulting record."""
        record = {}
        for stage in (self._sense, self._decide, self._actuate):
            record.update(stage(record, dt))
        return record

    def _sense(self, record: dict, dt: float) -> dict:
        return {"temperature": self.io.read_temperature(),
                "pressure": self.io.read_pressure()}

    def _decide(self, record: dict, dt: float) -> dict:
        command = self.control.step(record["temperature"], record["pressure"], dt=dt)
        return {"valve_pct": command}

    def _actuate(self, record: dict, dt: float) -> dict:
        return {"valve_counts": self.io.write_valve(record["valve_pct"]),
                "tripped": self.control.tripped}
