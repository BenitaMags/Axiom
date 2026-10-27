"""cycle_pipeline -- decomposes a supervisory cycle into discrete stages.

A single cycle is modelled as a small pipeline of steps applied to a shared
context object, rather than one flat function body.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, List


@dataclass
class CycleContext:
    """Carries the mutable state threaded through the cycle stages."""
    io: Any
    control: Any
    dt: float
    temperature: float = field(default=None)
    pressure: float = field(default=None)
    command: float = field(default=None)
    counts: Any = field(default=None)

    def to_record(self) -> dict:
        return {
            "temperature": self.temperature,
            "pressure": self.pressure,
            "valve_pct": self.command,
            "valve_counts": self.counts,
            "tripped": self.control.tripped,
        }


def _stage_read_temperature(ctx: CycleContext) -> None:
    ctx.temperature = ctx.io.read_temperature()


def _stage_read_pressure(ctx: CycleContext) -> None:
    ctx.pressure = ctx.io.read_pressure()


def _stage_compute_command(ctx: CycleContext) -> None:
    ctx.command = ctx.control.step(ctx.temperature, ctx.pressure, dt=ctx.dt)


def _stage_write_valve(ctx: CycleContext) -> None:
    ctx.counts = ctx.io.write_valve(ctx.command)


_STAGES: List[Callable[[CycleContext], None]] = [
    _stage_read_temperature,
    _stage_read_pressure,
    _stage_compute_command,
    _stage_write_valve,
]


class CyclePipeline:
    """Runs the fixed sequence of stages that make up one supervisory cycle."""

    def __init__(self, stages: List[Callable[[CycleContext], None]] = None):
        self._stages = stages if stages is not None else _STAGES

    def run(self, io, control, dt: float) -> dict:
        ctx = CycleContext(io=io, control=control, dt=dt)
        for stage in self._stages:
            stage(ctx)
        return ctx.to_record()
