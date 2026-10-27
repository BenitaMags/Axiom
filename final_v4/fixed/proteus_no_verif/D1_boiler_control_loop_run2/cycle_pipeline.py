"""cycle_pipeline -- stage-based decomposition of the supervisory cycle.

Splits the single `cycle` method into a small pipeline of discrete stages:
sensing, control, actuation, and reporting. Each stage is a plain function
operating on a shared context dict, so the sequence of side effects
(read temperature, read pressure, compute command, write valve) is
preserved exactly as in the original implementation.
"""


def _sense(supervisor, ctx):
    ctx["temperature"] = supervisor.io.read_temperature()
    ctx["pressure"] = supervisor.io.read_pressure()
    return ctx


def _control(supervisor, ctx):
    ctx["valve_pct"] = supervisor.control.step(
        ctx["temperature"], ctx["pressure"], dt=ctx["dt"]
    )
    return ctx


def _actuate(supervisor, ctx):
    ctx["valve_counts"] = supervisor.io.write_valve(ctx["valve_pct"])
    return ctx


def _report(supervisor, ctx):
    return {
        "temperature": ctx["temperature"],
        "pressure": ctx["pressure"],
        "valve_pct": ctx["valve_pct"],
        "valve_counts": ctx["valve_counts"],
        "tripped": supervisor.control.tripped,
    }


class CyclePipeline:
    """Runs the fixed sequence of stages that make up one supervisory cycle."""

    _STAGES = (_sense, _control, _actuate)

    def __init__(self, supervisor):
        self._supervisor = supervisor

    def run(self, dt: float) -> dict:
        ctx = {"dt": dt}
        for stage in self._STAGES:
            ctx = stage(self._supervisor, ctx)
        return _report(self._supervisor, ctx)
