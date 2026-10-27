"""cycle_pipeline -- decomposes one supervisory cycle into discrete stages."""


class CyclePipeline:
    """Runs a supervisory cycle as a sequence of read/control/actuate/report stages."""

    def __init__(self, supervisor):
        self._supervisor = supervisor

    def _read_sensors(self):
        io = self._supervisor.io
        return io.read_temperature(), io.read_pressure()

    def _compute_command(self, temperature, pressure, dt):
        return self._supervisor.control.step(temperature, pressure, dt=dt)

    def _actuate(self, command):
        return self._supervisor.io.write_valve(command)

    def _build_record(self, temperature, pressure, command, counts):
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": self._supervisor.control.tripped,
        }

    def run(self, dt: float) -> dict:
        temperature, pressure = self._read_sensors()
        command = self._compute_command(temperature, pressure, dt)
        counts = self._actuate(command)
        return self._build_record(temperature, pressure, command, counts)
