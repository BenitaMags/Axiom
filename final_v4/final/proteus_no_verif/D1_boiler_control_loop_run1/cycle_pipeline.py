"""cycle_pipeline -- staged execution of one supervisory cycle.

Decomposes the supervisory cycle into discrete stages (sense, decide, act,
report) rather than one flat function body.
"""


class CyclePipeline:
    """Runs the sense -> decide -> act -> report pipeline for a cycle."""

    def _sense(self, io):
        temperature = io.read_temperature()
        pressure = io.read_pressure()
        return temperature, pressure

    def _decide(self, control, temperature, pressure, dt):
        return control.step(temperature, pressure, dt=dt)

    def _act(self, io, command):
        return io.write_valve(command)

    def _report(self, temperature, pressure, command, counts, tripped):
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": tripped,
        }

    def run(self, io, control, dt) -> dict:
        temperature, pressure = self._sense(io)
        command = self._decide(control, temperature, pressure, dt)
        counts = self._act(io, command)
        return self._report(temperature, pressure, command, counts, control.tripped)
