"""cycle_pipeline -- decomposes a supervisory cycle into discrete stages.

A pipeline of small stages replaces the single inline sequence of
statements that used to live directly in BoilerSupervisor.cycle().
"""


class CycleContext:
    """Carries state as it flows through the pipeline stages."""

    __slots__ = (
        "io", "control", "dt",
        "temperature", "pressure", "command", "counts",
    )

    def __init__(self, io, control, dt):
        self.io = io
        self.control = control
        self.dt = dt
        self.temperature = None
        self.pressure = None
        self.command = None
        self.counts = None


def _read_temperature(ctx: CycleContext) -> CycleContext:
    ctx.temperature = ctx.io.read_temperature()
    return ctx


def _read_pressure(ctx: CycleContext) -> CycleContext:
    ctx.pressure = ctx.io.read_pressure()
    return ctx


def _compute_command(ctx: CycleContext) -> CycleContext:
    ctx.command = ctx.control.step(ctx.temperature, ctx.pressure, dt=ctx.dt)
    return ctx


def _actuate(ctx: CycleContext) -> CycleContext:
    ctx.counts = ctx.io.write_valve(ctx.command)
    return ctx


class CyclePipeline:
    """Runs the fixed sequence of stages that make up one control cycle."""

    _STAGES = (
        _read_temperature,
        _read_pressure,
        _compute_command,
        _actuate,
    )

    def run(self, io, control, dt: float) -> dict:
        ctx = CycleContext(io, control, dt)
        for stage in self._STAGES:
            ctx = stage(ctx)
        return self._to_record(ctx)

    @staticmethod
    def _to_record(ctx: CycleContext) -> dict:
        return {
            "temperature": ctx.temperature,
            "pressure": ctx.pressure,
            "valve_pct": ctx.command,
            "valve_counts": ctx.counts,
            "tripped": ctx.control.tripped,
        }
