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
        if current < cfg.inst_current:
            # Inverse-time accumulation with decay when below pickup.
            if current <= cfg.pickup_current:
                _r0_accum = _r0_accum - dt * 0.5
                if _r0_accum < 0.0:
                    _r0_accum = 0.0
            else:
                _r2_ratio = current / cfg.pickup_current
                _r0_accum = _r0_accum + dt * _r2_ratio * _r2_ratio
            if _r0_accum >= cfg.trip_energy:
                _r5_trip = True
        else:
            _r5_trip = True

        # Voltage / frequency protection.
        if voltage >= cfg.nominal_voltage * (1.0 + cfg.ov_pct):
            _r5_trip = True
        if frequency <= cfg.freq_low or frequency >= cfg.freq_high:
            _r5_trip = True

        if _r5_trip:
            _r4_timer = 0.0
            _r1_mode = "TRIPPED"
            _r0_accum = 0.0

    elif _r1_mode == "TRIPPED":
        _r4_timer = _r4_timer + dt
        if _r4_timer >= cfg.reclose_delay:
            if _r3_reclose_count >= cfg.max_reclose:
                _r1_mode = "LOCKOUT"
            else:
                _r4_timer = 0.0
                _r1_mode = "RECLOSING"

    elif _r1_mode == "RECLOSING":
        _r0_accum = 0.0
        _r3_reclose_count = _r3_reclose_count + 1
        _r1_mode = "CLOSED"   # re-energize; a persistent fault will re-trip next step

    elif _r1_mode == "LOCKOUT":
        pass  # latched until a manual reset (not modeled)

    return RelayState(_r1_mode, _r0_accum, _r4_timer, _r3_reclose_count)


def run_scenario() -> list:
    """Deterministic scenario: a temporary fault (reclose succeeds) followed by
    a permanent fault (drives the relay to LOCKOUT). Returns [(mode,)] samples."""
    _r0_cfg = RelayConfig(
        pickup_current=100.0, inst_current=800.0, trip_energy=6.0,
        nominal_voltage=230.0, ov_pct=0.15,
        freq_low=49.0, freq_high=51.0,
        reclose_delay=1.0, max_reclose=2,
    )
    _r5_state = RelayState(mode="CLOSED", accum=0.0, timer=0.0, reclose_count=0)

    _r4_samples = []
    _r2_dt = 0.05
    for step in range(400):
        _r7_voltage = 230.0
        _r6_t = step * _r2_dt
        _r3_frequency = 50.0
        # Temporary overload 2.0-3.0 s (clears on its own), permanent from 10 s.
        if 2.0 <= _r6_t < 3.0:
            _r1_current = 260.0
        elif _r6_t < 10.0:
            _r1_current = 60.0
        else:
            _r1_current = 300.0
        _r5_state = relay_step(_r5_state, _r1_current, _r7_voltage, _r3_frequency, _r0_cfg, _r2_dt)
        _r4_samples.append((_r5_state.mode, round(_r5_state.accum, 4), _r5_state.reclose_count))
    return _r4_samples


if __name__ == "__main__":
    out = run_scenario()
    print(f"steps={len(out)}")
    print("final mode:", out[-1][0], " reclose_count:", out[-1][2])
    print("modes seen:", sorted({m for m, _, _ in out}))
