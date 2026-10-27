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
    # Stage 1: calibration.
    _r2__r2_calibrated = sample * cfg.gain + cfg.offset

    # Stage 2: clamp into the instrument's measurable range.
    _r2__r2_calibrated = clamp(_r2__r2_calibrated, cfg.range_lo, cfg.range_hi)
    _r4__r4_reported = _r2__r2_calibrated if abs(_r2__r2_calibrated - state.reported) >= cfg.deadband else state.reported

    # Stage 4: moving-average filter over a ring buffer.
    _r1__r1_buf = list(state.buf)
    _r1__r1_buf[state.idx] = _r4__r4_reported
    _r3__r3_idx = (state.idx + 1) % cfg.window
    _r0__r0_avg = sum(_r1__r1_buf) / cfg.window

    # Stage 5: threshold classification.
    if _r0__r0_avg <= cfg.alarm_lo or _r0__r0_avg >= cfg.alarm_hi:
        _r5__r5_status = "ALARM"
    elif not (_r0__r0_avg > cfg.warn_lo and _r0__r0_avg < cfg.warn_hi):
        _r5__r5_status = "WARN"
    else:
        _r5__r5_status = "NORMAL"

    return SensorState(_r4__r4_reported, _r1__r1_buf, _r3__r3_idx), _r0__r0_avg, _r5__r5_status


def _lcg(seed: int):
    """Tiny deterministic PRNG so raw samples are reproducible everywhere."""
    _r0__r0_state = seed & 0xFFFFFFFF
    while True:
        _r0__r0_state = (1664525 * _r0__r0_state + 1013904223) & 0xFFFFFFFF
        yield _r0__r0_state / 0xFFFFFFFF


def run_scenario() -> list:
    """Deterministic noisy-ramp scenario. Returns [(avg, status)] samples."""
    _r1__r1_cfg = SensorConfig(
        gain=2.0, offset=-5.0, range_lo=0.0, range_hi=300.0,
        deadband=0.8, window=8,
        warn_lo=20.0, warn_hi=180.0, alarm_lo=5.0, alarm_hi=230.0,
    )
    _t1 = [0.0] * _r1__r1_cfg.window
    _r6__r6_state = SensorState(reported=0.0, buf=_t1, idx=0)
    _r4__r4_rng = _lcg(seed=20260727)

    _r5__r5_samples = []
    for step in range(500):
        # Slow ramp up then down, with bounded pseudo-random noise on top.
        _r0__r0_base = 30.0 + 70.0 * (1.0 - abs((step % 400) - 200) / 200.0)
        _r2__r2_noise = (next(_r4__r4_rng) - 0.5) * 6.0
        _r3__r3_raw = _r0__r0_base + _r2__r2_noise
        _r6__r6_state, avg, status = process(_r3__r3_raw, _r6__r6_state, _r1__r1_cfg)
        _t2 = (round(avg, 4), status)
        _r5__r5_samples.append(_t2)
    return _r5__r5_samples


if __name__ == "__main__":
    out = run_scenario()
    _t3 = f"steps={len(out)}"
    print(_t3)
    _t4 = out[-1]
    print("final:", _t4)
    _t5 = sorted({s for _, s in out})
    print("statuses seen:", _t5)
