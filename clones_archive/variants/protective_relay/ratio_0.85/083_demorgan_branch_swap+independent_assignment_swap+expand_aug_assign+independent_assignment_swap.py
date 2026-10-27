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
    mode = state.mode
    accum = state.accum
    timer = state.timer
    reclose_count = state.reclose_count

    if mode == "CLOSED":
        trip = False

        # Instantaneous overcurrent.
        if current < cfg.inst_current:
            # Inverse-time accumulation with decay when below pickup.
            if current <= cfg.pickup_current:
                accum = accum - dt * 0.5
                if accum < 0.0:
                    accum = 0.0
            else:
                ratio = current / cfg.pickup_current
                accum = accum + dt * ratio * ratio
            if accum >= cfg.trip_energy:
                trip = True
        else:
            trip = True

        # Voltage / frequency protection.
        if voltage >= cfg.nominal_voltage * (1.0 + cfg.ov_pct):
            trip = True
        if frequency <= cfg.freq_low or frequency >= cfg.freq_high:
            trip = True

        if trip:
            mode = "TRIPPED"
            timer = 0.0
            accum = 0.0

    elif mode == "TRIPPED":
        timer = timer + dt
        if timer >= cfg.reclose_delay:
            if reclose_count >= cfg.max_reclose:
                mode = "LOCKOUT"
            else:
                mode = "RECLOSING"
                timer = 0.0

    elif mode == "RECLOSING":
        mode = "CLOSED"   # re-energize; a persistent fault will re-trip next step
        reclose_count = reclose_count + 1
        accum = 0.0

    elif mode == "LOCKOUT":
        pass  # latched until a manual reset (not modeled)

    return RelayState(mode, accum, timer, reclose_count)


def run_scenario() -> list:
    """Deterministic scenario: a temporary fault (reclose succeeds) followed by
    a permanent fault (drives the relay to LOCKOUT). Returns [(mode,)] samples."""
    cfg = RelayConfig(
        pickup_current=100.0, inst_current=800.0, trip_energy=6.0,
        nominal_voltage=230.0, ov_pct=0.15,
        freq_low=49.0, freq_high=51.0,
        reclose_delay=1.0, max_reclose=2,
    )
    state = RelayState(mode="CLOSED", accum=0.0, timer=0.0, reclose_count=0)
    dt = 0.05

    samples = []
    for step in range(400):
        t = step * dt
        voltage = 230.0
        frequency = 50.0
        # Temporary overload 2.0-3.0 s (clears on its own), permanent from 10 s.
        if 2.0 <= t < 3.0:
            current = 260.0
        elif t < 10.0:
            current = 60.0
        else:
            current = 300.0
        state = relay_step(state, current, voltage, frequency, cfg, dt)
        samples.append((state.mode, round(state.accum, 4), state.reclose_count))
    return samples


if __name__ == "__main__":
    out = run_scenario()
    print(f"steps={len(out)}")
    print("final mode:", out[-1][0], " reclose_count:", out[-1][2])
    print("modes seen:", sorted({m for m, _, _ in out}))
