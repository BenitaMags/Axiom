"""cycle_pipeline -- decomposes a supervisory cycle into discrete stages."""


class CyclePipeline:
    """Runs a boiler supervisory cycle as a sequence of stages, passing a
    shared context dict between them.
    """

    def __init__(self, supervisor):
        self._supervisor = supervisor
        self._stages = (
            self._read_sensors,
            self._compute_command,
            self._actuate,
            self._finalize,
        )

    def run(self, dt: float) -> dict:
        ctx = {"dt": dt}
        for stage in self._stages:
            stage(ctx)
        return ctx["record"]

    def _read_sensors(self, ctx: dict) -> None:
        io = self._supervisor.io
        ctx["temperature"] = io.read_temperature()
        ctx["pressure"] = io.read_pressure()

    def _compute_command(self, ctx: dict) -> None:
        control = self._supervisor.control
        ctx["command"] = control.step(
            ctx["temperature"], ctx["pressure"], dt=ctx["dt"]
        )

    def _actuate(self, ctx: dict) -> None:
        ctx["counts"] = self._supervisor.io.write_valve(ctx["command"])

    def _finalize(self, ctx: dict) -> None:
        ctx["record"] = {
            "temperature": ctx["temperature"],
            "pressure": ctx["pressure"],
            "valve_pct": ctx["command"],
            "valve_counts": ctx["counts"],
            "tripped": self._supervisor.control.tripped,
        }
