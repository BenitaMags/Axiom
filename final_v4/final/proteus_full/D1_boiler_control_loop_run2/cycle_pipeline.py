"""cycle_pipeline -- stage-based decomposition of one supervisory cycle.

Each stage takes the accumulated context dict and returns the value it
contributes; the pipeline threads them together in the same order the
original monolithic `cycle` method used.
"""


class CyclePipeline:
    """Runs the read -> control -> actuate stages for a supervisory cycle."""

    def __init__(self):
        self._stages = (
            self._read_temperature,
            self._read_pressure,
            self._compute_command,
            self._actuate_valve,
        )

    def run(self, io, control, dt: float) -> dict:
        ctx = {"io": io, "control": control, "dt": dt}
        for stage in self._stages:
            stage(ctx)
        return {
            "temperature": ctx["temperature"],
            "pressure": ctx["pressure"],
            "valve_pct": ctx["command"],
            "valve_counts": ctx["counts"],
            "tripped": ctx["control"].tripped,
        }

    @staticmethod
    def _read_temperature(ctx: dict) -> None:
        ctx["temperature"] = ctx["io"].read_temperature()

    @staticmethod
    def _read_pressure(ctx: dict) -> None:
        ctx["pressure"] = ctx["io"].read_pressure()

    @staticmethod
    def _compute_command(ctx: dict) -> None:
        ctx["command"] = ctx["control"].step(
            ctx["temperature"], ctx["pressure"], dt=ctx["dt"]
        )

    @staticmethod
    def _actuate_valve(ctx: dict) -> None:
        ctx["counts"] = ctx["io"].write_valve(ctx["command"])
