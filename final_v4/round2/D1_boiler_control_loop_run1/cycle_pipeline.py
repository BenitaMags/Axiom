"""cycle_pipeline -- stage-based decomposition of a supervisory cycle.

Breaks the single-shot supervisory cycle into discrete stages (sense,
control, actuate, report) that are executed in sequence, threading a
small context object between them.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class CycleContext:
    temperature: float = None
    pressure: float = None
    command: float = None
    counts: Any = None
    tripped: bool = None


class CyclePipeline:
    """Runs the sense -> control -> actuate -> report stages in order."""

    def run(self, io, control, dt: float) -> dict:
        ctx = CycleContext()
        for stage in (self._sense, self._control, self._actuate, self._report):
            stage(io, control, dt, ctx)
        return {
            "temperature": ctx.temperature,
            "pressure": ctx.pressure,
            "valve_pct": ctx.command,
            "valve_counts": ctx.counts,
            "tripped": ctx.tripped,
        }

    @staticmethod
    def _sense(io, control, dt, ctx: CycleContext) -> None:
        ctx.temperature = io.read_temperature()
        ctx.pressure = io.read_pressure()

    @staticmethod
    def _control(io, control, dt, ctx: CycleContext) -> None:
        ctx.command = control.step(ctx.temperature, ctx.pressure, dt=dt)

    @staticmethod
    def _actuate(io, control, dt, ctx: CycleContext) -> None:
        ctx.counts = io.write_valve(ctx.command)

    @staticmethod
    def _report(io, control, dt, ctx: CycleContext) -> None:
        ctx.tripped = control.tripped
