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
    _r0__spt_t1 = sample * cfg.gain
    # Stage 1: calibration.
    _r4_calibrated = _r0__spt_t1 + cfg.offset

    # Stage 2: clamp into the instrument's measurable range.
    _r4_calibrated = clamp(_r4_calibrated, cfg.range_lo, cfg.range_hi)

    # Stage 3: deadband -- ignore jitter smaller than the deadband.
    if abs(_r4_calibrated - state.reported) < cfg.deadband:
        _r6_reported = state.reported
    else:
        _r6_reported = _r4_calibrated

    # Stage 4: moving-average filter over a ring buffer.
    _r3_buf = list(state.buf)
    _r3_buf[state.idx] = _r6_reported
    _r1__spt_t2 = (state.idx + 1)
    _r5_idx = _r1__spt_t2 % cfg.window
    _r2_avg = sum(_r3_buf) / cfg.window

    # Stage 5: threshold classification.
    if _r2_avg <= cfg.alarm_lo or _r2_avg >= cfg.alarm_hi:
        _r7_status = "ALARM"
    elif _r2_avg <= cfg.warn_lo or _r2_avg >= cfg.warn_hi:
        _r7_status = "WARN"
    else:
        _r7_status = "NORMAL"

    return SensorState(_r6_reported, _r3_buf, _r5_idx), _r2_avg, _r7_status


def _lcg(seed: int):
    """Tiny deterministic PRNG so raw samples are reproducible everywhere."""
    _r2_state = seed & 0xFFFFFFFF
    while True:
        _r1__spt_t5 = 1664525 * _r2_state
        _r0__spt_t3 = (_r1__spt_t5 + 1013904223)
        _r2_state = _r0__spt_t3 & 0xFFFFFFFF
        yield _r2_state / 0xFFFFFFFF


def run_scenario() -> list:
    """Deterministic noisy-ramp scenario. Returns [(avg, status)] samples."""
    _r5_cfg = SensorConfig(
        gain=2.0, offset=-5.0, range_lo=0.0, range_hi=300.0,
        deadband=0.8, window=8,
        warn_lo=20.0, warn_hi=180.0, alarm_lo=5.0, alarm_hi=230.0,
    )
    _r2__t1 = [0.0] * _r5_cfg.window
    _r10_state = SensorState(reported=0.0, buf=_r2__t1, idx=0)
    _r8_rng = _lcg(seed=20260727)

    _r9_samples = []
    _r0__lv0 = 0
    while _r0__lv0 < 500:
        _r11_step = _r0__lv0
        # Slow ramp up then down, with bounded pseudo-random noise on top.
        _r4_base = 30.0 + 70.0 * (1.0 - abs((_r11_step % 400) - 200) / 200.0)
        _r1__spt_t4 = (next(_r8_rng) - 0.5)
        _r6_noise = _r1__spt_t4 * 6.0
        _r7_raw = _r4_base + _r6_noise
        _r10_state, avg, status = process(_r7_raw, _r10_state, _r5_cfg)
        _r3__t2 = (round(avg, 4), status)
        _r9_samples.append(_r3__t2)
        _r0__lv0 += 1
    return _r9_samples


if __name__ == "__main__":
    out = run_scenario()
    _t3 = f"steps={len(out)}"
    print(_t3)
    _t4 = out[-1]
    print("final:", _t4)
    _t7 = {s for _, s in out}
    _t5 = sorted(_t7)
    print("statuses seen:", _t5)
