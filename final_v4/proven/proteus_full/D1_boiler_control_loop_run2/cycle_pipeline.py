"""cycle_pipeline -- stage-based execution of a supervisory cycle.

Splits the supervisory cycle into discrete stages (sense, control, actuate,
report) run in sequence, instead of one flat function body.
"""


class CyclePipeline:
    """Runs the sense -> control -> actuate -> report stages in order."""

    def run(self, io, control, dt: float) -> dict:
        sensed = self._sense(io)
        command = self._control(control, sensed, dt)
        counts = self._actuate(io, command)
        return self._report(sensed, command, counts, control)

    @staticmethod
    def _sense(io) -> dict:
        return {
            "temperature": io.read_temperature(),
            "pressure": io.read_pressure(),
        }

    @staticmethod
    def _control(control, sensed: dict, dt: float) -> float:
        return control.step(sensed["temperature"], sensed["pressure"], dt=dt)

    @staticmethod
    def _actuate(io, command: float):
        return io.write_valve(command)

    @staticmethod
    def _report(sensed: dict, command: float, counts, control) -> dict:
        return {
            "temperature": sensed["temperature"],
            "pressure": sensed["pressure"],
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": control.tripped,
        }
