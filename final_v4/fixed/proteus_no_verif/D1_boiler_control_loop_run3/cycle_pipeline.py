"""cycle_pipeline -- stage-based decomposition of one supervisory cycle."""


class CyclePipeline:
    """Runs a supervisory cycle as a sequence of discrete stages."""

    def __init__(self, supervisor):
        self._supervisor = supervisor

    def run(self, dt: float) -> dict:
        context = {}
        for stage in (self._read_sensors, self._compute_command, self._actuate, self._assemble_record):
            stage(context, dt)
        return context["record"]

    def _read_sensors(self, context: dict, dt: float) -> None:
        io = self._supervisor.io
        context["temperature"] = io.read_temperature()
        context["pressure"] = io.read_pressure()

    def _compute_command(self, context: dict, dt: float) -> None:
        control = self._supervisor.control
        context["command"] = control.step(context["temperature"], context["pressure"], dt=dt)

    def _actuate(self, context: dict, dt: float) -> None:
        context["counts"] = self._supervisor.io.write_valve(context["command"])

    def _assemble_record(self, context: dict, dt: float) -> None:
        context["record"] = {
            "temperature": context["temperature"],
            "pressure": context["pressure"],
            "valve_pct": context["command"],
            "valve_counts": context["counts"],
            "tripped": self._supervisor.control.tripped,
        }
