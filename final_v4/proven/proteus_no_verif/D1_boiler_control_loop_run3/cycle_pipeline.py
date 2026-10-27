"""cycle_pipeline -- stage-based execution of one supervisory cycle.

Decomposes the supervisory cycle into discrete stages (sense, decide, act,
report) instead of one flat function, while preserving the exact order of
side effects and the exact return value produced by the original
implementation.
"""


class CyclePipeline:
    """Runs a single supervisory cycle as a sequence of stages."""

    def run(self, io, control, dt: float) -> dict:
        readings = self._sense(io)
        command = self._decide(control, readings, dt)
        counts = self._act(io, command)
        return self._report(readings, command, counts, control)

    @staticmethod
    def _sense(io) -> dict:
        temperature = io.read_temperature()
        pressure = io.read_pressure()
        return {"temperature": temperature, "pressure": pressure}

    @staticmethod
    def _decide(control, readings: dict, dt: float):
        return control.step(readings["temperature"], readings["pressure"], dt=dt)

    @staticmethod
    def _act(io, command):
        return io.write_valve(command)

    @staticmethod
    def _report(readings: dict, command, counts, control) -> dict:
        return {
            "temperature": readings["temperature"],
            "pressure": readings["pressure"],
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": control.tripped,
        }
