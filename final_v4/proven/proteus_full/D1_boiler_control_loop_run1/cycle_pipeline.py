"""cycle_pipeline -- stage-based execution of one supervisory cycle.

Decomposes BoilerSupervisor.cycle into discrete stages (sense, control,
actuate, report) run in sequence, instead of one flat function body.
"""


class CyclePipeline:
    def __init__(self, supervisor):
        self._supervisor = supervisor

    def _sense(self):
        io = self._supervisor.io
        temperature = io.read_temperature()
        pressure = io.read_pressure()
        return temperature, pressure

    def _control(self, temperature, pressure, dt):
        return self._supervisor.control.step(temperature, pressure, dt=dt)

    def _actuate(self, command):
        return self._supervisor.io.write_valve(command)

    def _report(self, temperature, pressure, command, counts):
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": self._supervisor.control.tripped,
        }

    def run(self, dt: float) -> dict:
        temperature, pressure = self._sense()
        command = self._control(temperature, pressure, dt)
        counts = self._actuate(command)
        return self._report(temperature, pressure, command, counts)
