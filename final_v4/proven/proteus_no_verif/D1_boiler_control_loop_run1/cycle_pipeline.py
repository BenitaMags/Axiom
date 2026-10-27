"""cycle_pipeline -- decomposes a supervisory cycle into discrete stages."""


class CyclePipeline:
    """Runs the read -> control -> actuate -> report pipeline for a cycle."""

    def run(self, io, control, dt: float) -> dict:
        temperature, pressure = self._read_sensors(io)
        command = self._compute_command(control, temperature, pressure, dt)
        counts = self._actuate(io, command)
        return self._build_record(temperature, pressure, command, counts, control)

    @staticmethod
    def _read_sensors(io):
        temperature = io.read_temperature()
        pressure = io.read_pressure()
        return temperature, pressure

    @staticmethod
    def _compute_command(control, temperature, pressure, dt):
        return control.step(temperature, pressure, dt=dt)

    @staticmethod
    def _actuate(io, command):
        return io.write_valve(command)

    @staticmethod
    def _build_record(temperature, pressure, command, counts, control) -> dict:
        return {
            "temperature": temperature,
            "pressure": pressure,
            "valve_pct": command,
            "valve_counts": counts,
            "tripped": control.tripped,
        }
