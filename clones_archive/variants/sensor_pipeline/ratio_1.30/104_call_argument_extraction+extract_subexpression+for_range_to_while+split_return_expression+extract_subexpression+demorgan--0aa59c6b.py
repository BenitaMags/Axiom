"""Sensor / gauge signal-conditioning pipeline -- measurement instrument logic.

Takes a raw ADC-style reading and pushes it through the stages a real gauge
front-end uses before reporting a value and an alarm status:

    raw --> calibrate (gain/offset) --> range clamp --> deadband -->
            moving-average filter (ring buffer) --> threshold classify

Status is one of NORMAL / WARN / ALARM based on the filtered value.

Control-flow of interest for a diversifier:
  * staged data flow (each stage is a candidate for reordering guards)
  * ring-buffer loop with modular index arithmetic
  * multi-way threshold classification (nested elif chain)
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class SensorConfig:
    gain: float
    offset: float
    range_lo: float
    range_hi: float
    deadband: float
    window: int
    warn_lo: float
    warn_hi: float
    alarm_lo: float
    alarm_hi: float


@dataclass
class SensorState:
    reported: float
    buf: List[float]
    idx: int


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def process(sample: float, state: SensorState, cfg: SensorConfig) -> tuple:
    """Run one raw sample through the pipeline. Returns (new_state, avg, status)."""
    _spt_t1 = sample * cfg.gain
    # Stage 1: calibration.
    calibrated = _spt_t1 + cfg.offset

    # Stage 2: clamp into the instrument's measurable range.
    calibrated = clamp(calibrated, cfg.range_lo, cfg.range_hi)

    # Stage 3: deadband -- ignore jitter smaller than the deadband.
    if abs(calibrated - state.reported) >= cfg.deadband:
        reported = calibrated
    else:
        reported = state.reported

    # Stage 4: moving-average filter over a ring buffer.
    buf = list(state.buf)
    buf[state.idx] = reported
    _spt_t2 = (state.idx + 1)
    idx = _spt_t2 % cfg.window
    avg = sum(buf) / cfg.window

    # Stage 5: threshold classification.
    if avg <= cfg.alarm_lo or avg >= cfg.alarm_hi:
        status = "ALARM"
    elif avg > cfg.warn_lo and avg < cfg.warn_hi:
        status = "NORMAL"
    else:
        status = "WARN"
    _spt_r1 = SensorState(reported, buf, idx), avg, status

    return _spt_r1


def _lcg(seed: int):
    """Tiny deterministic PRNG so raw samples are reproducible everywhere."""
    state = seed & 0xFFFFFFFF
    while True:
        _spt_t5 = 1664525 * state
        _spt_t3 = (_spt_t5 + 1013904223)
        state = _spt_t3 & 0xFFFFFFFF
        yield state / 0xFFFFFFFF


def run_scenario() -> list:
    """Deterministic noisy-ramp scenario. Returns [(avg, status)] samples."""
    cfg = SensorConfig(
        gain=2.0, offset=-5.0, range_lo=0.0, range_hi=300.0,
        deadband=0.8, window=8,
        warn_lo=20.0, warn_hi=180.0, alarm_lo=5.0, alarm_hi=230.0,
    )
    _t1 = [0.0] * cfg.window
    state = SensorState(reported=0.0, buf=_t1, idx=0)
    rng = _lcg(seed=20260727)

    samples = []
    _lv0 = 0
    while _lv0 < 500:
        step = _lv0
        # Slow ramp up then down, with bounded pseudo-random noise on top.
        base = 30.0 + 70.0 * (1.0 - abs((step % 400) - 200) / 200.0)
        _spt_t4 = (next(rng) - 0.5)
        noise = _spt_t4 * 6.0
        raw = base + noise
        state, avg, status = process(raw, state, cfg)
        _t2 = (round(avg, 4), status)
        samples.append(_t2)
        _lv0 += 1
    return samples


if __name__ == "__main__":
    out = run_scenario()
    _t3 = f"steps={len(out)}"
    print(_t3)
    _t4 = out[-1]
    print("final:", _t4)
    _t5 = sorted({s for _, s in out})
    print("statuses seen:", _t5)
