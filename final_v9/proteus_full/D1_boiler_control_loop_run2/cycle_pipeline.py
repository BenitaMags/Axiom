"""cycle_pipeline -- staged execution of a single supervisory cycle.

Decomposes the read/control/actuate sequence into discrete stages that
are executed in order, threading a shared context between them.
"""


class _CycleContext:
    """Carries the intermediate state produced by each pipeline stage."""

    __slots__ = ("temperature", "pressure", "command", "counts", "tripped")

    def __init__(self):
        self.temperature = None
        self.pressure = None
        self.command = None
        self.counts = None
        self.tripped = None

    def as_record(self) -> dict:
        return {
            "temperature": self.temperature,
            "pressure": self.pressure,
            "valve_pct": self.command,
            "valve_counts": self.counts,
            "tripped": self.tripped,
        }


def _read_sensors_stage(io, control, dt, ctx):
    ctx.temperature = io.read_temperature()
    ctx.pressure = io.read_pressure()


def _control_stage(io, control, dt, ctx):
    ctx.command = control.step(ctx.temperature, ctx.pressure, dt=dt)


def _actuate_stage(io, control, dt, ctx):
    ctx.counts = io.write_valve(ctx.command)


def _finalize_stage(io, control, dt, ctx):
    ctx.tripped = control.tripped


class CyclePipeline:
    """Runs the fixed sequence of stages that make up one supervisory cycle."""

    def __init__(self):
        self._stages = (
            _read_sensors_stage,
            _control_stage,
            _actuate_stage,
            _finalize_stage,
        )

    def run(self, io, control, dt: float) -> dict:
        ctx = _CycleContext()
        for stage in self._stages:
            stage(io, control, dt, ctx)
        return ctx.as_record()
