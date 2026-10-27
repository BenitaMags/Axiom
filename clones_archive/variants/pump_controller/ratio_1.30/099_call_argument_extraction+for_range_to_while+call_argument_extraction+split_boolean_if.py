"""Water-tank pump controller -- hysteresis (bang-bang) state machine.

Infrastructure-style level control. The pump refills a tank against a varying
demand draw. Control is on/off with a deadband: pump turns ON at the low
setpoint, OFF at the high setpoint. Safety interlocks force a FAULT latch on
overflow or on excessive continuous runtime (dry-run / stuck-valve proxy).

States: OFF -> ON -> OFF (normal cycle), any -> FAULT (latched).

Control-flow of interest for a diversifier:
  * explicit finite state machine (string-tagged modes)
  * hysteresis branch that depends on current mode
  * latching fault logic that overrides normal transitions
"""

from dataclasses import dataclass


@dataclass
class PumpState:
    level: float
    mode: str      # "OFF" | "ON" | "FAULT"
    runtime: float


@dataclass
class PumpConfig:
    low: float
    high: float
    overflow: float
    pump_rate: float
    max_runtime: float


def pump_step(state: PumpState, demand: float, cfg: PumpConfig,
              dt: float) -> PumpState:
    level = state.level
    mode = state.mode
    runtime = state.runtime

    # --- Hysteresis control (only while not faulted) ---
    if mode == "ON":
        runtime += dt
        if level >= cfg.high:
            mode = "OFF"
            runtime = 0.0
    elif mode == "OFF":
        if level <= cfg.low:
            mode = "ON"
            runtime = 0.0

    # --- Plant update ---
    inflow = cfg.pump_rate if mode == "ON" else 0.0
    level = level + dt * (inflow - demand)
    if level < 0.0:
        level = 0.0

    # --- Safety interlocks (latching) ---
    if level >= cfg.overflow:
        mode = "FAULT"
    if mode == "ON":
        if runtime > cfg.max_runtime:
            mode = "FAULT"

    if level > cfg.overflow:
        level = cfg.overflow

    return PumpState(level, mode, runtime)


def run_scenario() -> list:
    """Deterministic demand profile. Returns [(level, mode)] samples."""
    cfg = PumpConfig(low=20.0, high=80.0, overflow=100.0,
                     pump_rate=12.0, max_runtime=40.0)
    state = PumpState(level=50.0, mode="OFF", runtime=0.0)
    dt = 0.5

    samples = []
    _lv0 = 0
    while _lv0 < 400:
        step = _lv0
        # Demand rises and falls in phases; a heavy-draw phase stresses the pump.
        phase = step % 200
        if phase < 60:
            demand = 4.0
        elif phase < 120:
            demand = 10.5
        else:
            demand = 2.0
        state = pump_step(state, demand, cfg, dt)
        _t0 = (state.level, state.mode)
        samples.append(_t0)
        _lv0 += 1
    return samples


if __name__ == "__main__":
    out = run_scenario()
    _t1 = f"steps={len(out)}"
    print(_t1)
    _t2 = f"final level={out[-1][0]:.3f}  mode={out[-1][1]}"
    print(_t2)
    _t4 = {m for _, m in out}
    _t3 = sorted(_t4)
    print("modes seen:", _t3)
