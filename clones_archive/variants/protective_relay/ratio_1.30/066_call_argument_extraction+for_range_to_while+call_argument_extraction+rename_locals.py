"""Protective relay / circuit breaker -- electrical control-center logic.

Monitors current, voltage and frequency on a feeder and drives a breaker
through a four-state machine:

    CLOSED -> TRIPPED -> RECLOSING -> CLOSED   (successful auto-reclose)
    CLOSED -> TRIPPED -> ... -> LOCKOUT        (permanent fault, retries used up)

Protection functions:
  * instantaneous overcurrent  (I >= inst_current)
  * inverse-time overcurrent   (energy accumulator ~ (I/pickup)^2 * t)
  * over-voltage               (instantaneous)
  * under/over-frequency       (instantaneous)

Control-flow of interest for a diversifier:
  * multi-state machine with per-state branch blocks
  * accumulator with decay (loop-carried float state)
  * several independent trip conditions OR-ed together
"""

from dataclasses import dataclass


@dataclass
class RelayState:
    mode: str          # CLOSED | TRIPPED | RECLOSING | LOCKOUT
    accum: float       # inverse-time overcurrent accumulator
    timer: float       # dead-time timer while TRIPPED
    reclose_count: int


@dataclass
class RelayConfig:
    pickup_current: float
    inst_current: float
    trip_energy: float
    nominal_voltage: float
    ov_pct: float
    freq_low: float
    freq_high: float
    reclose_delay: float
    max_reclose: int


def relay_step(state: RelayState, current: float, voltage: float,
               frequency: float, cfg: RelayConfig, dt: float) -> RelayState:
    _r1_mode = state.mode
    _r0_accum = state.accum
    _r4_timer = state.timer
    _r3_reclose_count = state.reclose_count

    if _r1_mode == "CLOSED":
        _r5_trip = False

        # Instantaneous overcurrent.
        if current >= cfg.inst_current:
            _r5_trip = True
        else:
            # Inverse-time accumulation with decay when below pickup.
            if current > cfg.pickup_current:
                _r2_ratio = current / cfg.pickup_current
                _r0_accum += dt * _r2_ratio * _r2_ratio
            else:
                _r0_accum -= dt * 0.5
                if _r0_accum < 0.0:
                    _r0_accum = 0.0
            if _r0_accum >= cfg.trip_energy:
                _r5_trip = True

        # Voltage / frequency protection.
        if voltage >= cfg.nominal_voltage * (1.0 + cfg.ov_pct):
            _r5_trip = True
        if frequency <= cfg.freq_low or frequency >= cfg.freq_high:
            _r5_trip = True

        if _r5_trip:
            _r1_mode = "TRIPPED"
            _r4_timer = 0.0
            _r0_accum = 0.0

    elif _r1_mode == "TRIPPED":
        _r4_timer += dt
        if _r4_timer >= cfg.reclose_delay:
            if _r3_reclose_count < cfg.max_reclose:
                _r1_mode = "RECLOSING"
                _r4_timer = 0.0
            else:
                _r1_mode = "LOCKOUT"

    elif _r1_mode == "RECLOSING":
        _r3_reclose_count += 1
        _r0_accum = 0.0
        _r1_mode = "CLOSED"   # re-energize; a persistent fault will re-trip next step

    elif _r1_mode == "LOCKOUT":
        pass  # latched until a manual reset (not modeled)

    return RelayState(_r1_mode, _r0_accum, _r4_timer, _r3_reclose_count)


def run_scenario() -> list:
    """Deterministic scenario: a temporary fault (reclose succeeds) followed by
    a permanent fault (drives the relay to LOCKOUT). Returns [(mode,)] samples."""
    _r2_cfg = RelayConfig(
        pickup_current=100.0, inst_current=800.0, trip_energy=6.0,
        nominal_voltage=230.0, ov_pct=0.15,
        freq_low=49.0, freq_high=51.0,
        reclose_delay=1.0, max_reclose=2,
    )
    _r7_state = RelayState(mode="CLOSED", accum=0.0, timer=0.0, reclose_count=0)
    _r4_dt = 0.05

    _r6_samples = []
    _r0__lv0 = 0
    while _r0__lv0 < 400:
        _r8_step = _r0__lv0
        _r9_t = _r8_step * _r4_dt
        _r10_voltage = 230.0
        _r5_frequency = 50.0
        # Temporary overload 2.0-3.0 s (clears on its own), permanent from 10 s.
        if 2.0 <= _r9_t < 3.0:
            _r3_current = 260.0
        elif _r9_t >= 10.0:
            _r3_current = 300.0
        else:
            _r3_current = 60.0
        _r7_state = relay_step(_r7_state, _r3_current, _r10_voltage, _r5_frequency, _r2_cfg, _r4_dt)
        _r1__t0 = (_r7_state.mode, round(_r7_state.accum, 4), _r7_state.reclose_count)
        _r6_samples.append(_r1__t0)
        _r0__lv0 += 1
    return _r6_samples


if __name__ == "__main__":
    out = run_scenario()
    _t1 = f"steps={len(out)}"
    print(_t1)
    _t2 = out[-1][0]
    _t3 = out[-1][2]
    print("final mode:", _t2, " reclose_count:", _t3)
    _t5 = {m for m, _, _ in out}
    _t4 = sorted(_t5)
    print("modes seen:", _t4)
