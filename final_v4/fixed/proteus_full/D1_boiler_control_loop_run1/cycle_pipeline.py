"""cycle_pipeline -- stage-based decomposition of the supervisory cycle.

Each stage reads from / writes to a shared context object; the pipeline
runs the stages in sequence and returns the final record. This keeps the
supervisor's `cycle` method itself trivial while preserving exactly the
same sequence of I/O calls and the same resulting dictionary.
"""


class _CycleContext:
    __slots__ = (
        "supervisor",
        "dt",
        "temperature",
        "pressure",
        "command",
        "counts",
    )

    def __init__(self, supervisor, dt):
        self.supervisor = supervisor
        self.dt = dt
        self.temperature = None
        self.pressure = None
        self.command = None
        self.counts = None


def _stage_read_temperature(ctx: _CycleContext) -> None:
    ctx.temperature = ctx.supervisor.io.read_temperature()


def _stage_read_pressure(ctx: _CycleContext) -> None:
    ctx.pressure = ctx.supervisor.io.read_pressure()


def _stage_control_step(ctx: _CycleContext) -> None:
    ctx.command = ctx.supervisor.control.step(
        ctx.temperature, ctx.pressure, dt=ctx.dt
    )


def _stage_actuate(ctx: _CycleContext) -> None:
    ctx.counts = ctx.supervisor.io.write_valve(ctx.command)


def _stage_build_record(ctx: _CycleContext) -> dict:
    return {
        "temperature": ctx.temperature,
        "pressure": ctx.pressure,
        "valve_pct": ctx.command,
        "valve_counts": ctx.counts,
        "tripped": ctx.supervisor.control.tripped,
    }


_STAGES = (
    _stage_read_temperature,
    _stage_read_pressure,
    _stage_control_step,
    _stage_actuate,
)


class CyclePipeline:
    """Runs the fixed sequence of stages that make up one supervisory cycle."""

    def __init__(self, supervisor):
        self.supervisor = supervisor

    def run(self, dt: float) -> dict:
        ctx = _CycleContext(self.supervisor, dt)
        for stage in _STAGES:
            stage(ctx)
        return _stage_build_record(ctx)
