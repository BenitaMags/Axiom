"""cycle_pipeline -- stages composing one supervisory cycle.

Extracted so the ordering of read -> control -> actuate -> report is
expressed as an explicit pipeline of small steps rather than one flat
function body.
"""


class CyclePipeline:
    """Runs the fixed sequence of stages that make up a supervisory cycle."""

    def run(self, io, control, dt: float) -> dict:
        readings = self._read_sensors(io)
        command = self._compute_command(control, readings, dt)
        counts = self._actuate(io, command)
        return self._report(readings, command, counts, control)

    @staticmethod
    def _read_sensors(io) -> dict:
        return {
            "temperature": io.read_temperature(),
            "pressure": io.read_pressure(),
        }

    @staticmethod
    def _compute_command(control, readings: dict, dt: float) -> float:
        return control.step(readings["temperature"], readings["pressure"], dt=dt)

    @staticmethod
    def _actuate(io, command: float):
        return io.write_valve(command)

    @staticmethod
    def _report(readings: dict, command: float, counts, control) -> dict:
        return {
            "temperature": readings["temperature"],
            "pressure": readings["pressure"],
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": control.tripped,
        }
