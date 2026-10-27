"""cycle_pipeline -- decomposes a supervisory cycle into discrete stages.

Each stage takes the accumulating context dict and returns the value(s)
it produces; the pipeline stitches them together into the final record.
"""


class CyclePipeline:
    """Runs a fixed sequence of stages: sense, control, actuate, report."""

    def run(self, io, control, dt: float) -> dict:
        temperature, pressure = self._sense(io)
        command = self._control(control, temperature, pressure, dt)
        counts = self._actuate(io, command)
        return self._report(temperature, pressure, command, counts, control)

    @staticmethod
    def _sense(io):
        temperature = io.read_temperature()
        pressure = io.read_pressure()
        return temperature, pressure

    @staticmethod
    def _control(control, temperature, pressure, dt):
        return control.step(temperature, pressure, dt=dt)

    @staticmethod
    def _actuate(io, command):
        return io.write_valve(command)

    @staticmethod
    def _report(temperature, pressure, command, counts, control) -> dict:
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": control.tripped,
        }
