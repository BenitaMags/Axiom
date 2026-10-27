"""cycle_pipeline -- stages composing one supervisory cycle."""


class CyclePipeline:
    """Runs the read -> control -> actuate stages for a supervisor."""

    def __init__(self, supervisor):
        self._supervisor = supervisor

    def run(self, dt: float) -> dict:
        supervisor = self._supervisor

        temperature, pressure = self._read_sensors(supervisor)
        command = self._compute_command(supervisor, temperature, pressure, dt)
        counts = self._actuate(supervisor, command)

        return self._build_record(temperature, pressure, command, counts, supervisor)

    @staticmethod
    def _read_sensors(supervisor):
        temperature = supervisor.io.read_temperature()
        pressure = supervisor.io.read_pressure()
        return temperature, pressure

    @staticmethod
    def _compute_command(supervisor, temperature, pressure, dt):
        return supervisor.control.step(temperature, pressure, dt=dt)

    @staticmethod
    def _actuate(supervisor, command):
        return supervisor.io.write_valve(command)

    @staticmethod
    def _build_record(temperature, pressure, command, counts, supervisor) -> dict:
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": supervisor.control.tripped,
        }
