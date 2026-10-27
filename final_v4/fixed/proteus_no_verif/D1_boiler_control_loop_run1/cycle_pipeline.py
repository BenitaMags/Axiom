"""cycle_pipeline -- decomposes the supervisory cycle into discrete stages.

Each stage takes and returns a shared context, forming a small pipeline
that reads sensors, computes a control action, actuates the valve, and
finally assembles the resulting record.
"""


class _CycleContext:
    """Mutable bag of state threaded through the pipeline stages."""

    def __init__(self, supervisor, dt):
        self.supervisor = supervisor
        self.dt = dt
        self.temperature = None
        self.pressure = None
        self.command = None
        self.counts = None


def _read_temperature(ctx: _CycleContext) -> _CycleContext:
    ctx.temperature = ctx.supervisor.io.read_temperature()
    return ctx


def _read_pressure(ctx: _CycleContext) -> _CycleContext:
    ctx.pressure = ctx.supervisor.io.read_pressure()
    return ctx


def _compute_command(ctx: _CycleContext) -> _CycleContext:
    ctx.command = ctx.supervisor.control.step(
        ctx.temperature, ctx.pressure, dt=ctx.dt
    )
    return ctx


def _actuate_valve(ctx: _CycleContext) -> _CycleContext:
    ctx.counts = ctx.supervisor.io.write_valve(ctx.command)
    return ctx


def _build_record(ctx: _CycleContext) -> dict:
    return {
        "temperature": ctx.temperature,
        "pressure": ctx.pressure,
        "valve_pct": ctx.command,
        "valve_counts": ctx.counts,
        "tripped": ctx.supervisor.control.tripped,
    }


class CyclePipeline:
    """Runs the ordered stages of a supervisory cycle for a given supervisor."""

    _STAGES = (
        _read_temperature,
        _read_pressure,
        _compute_command,
        _actuate_valve,
    )

    def __init__(self, supervisor):
        self.supervisor = supervisor

    def run(self, dt: float) -> dict:
        ctx = _CycleContext(self.supervisor, dt)
        for stage in self._STAGES:
            ctx = stage(ctx)
        return _build_record(ctx)
