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
    _r2_level = state.level
    _r3_mode = state.mode
    _r4_runtime = state.runtime

    # --- Hysteresis control (only while not faulted) ---
    if _r3_mode == "ON":
        _r4_runtime += dt
        if _r2_level >= cfg.high:
            _r3_mode = "OFF"
            _r4_runtime = 0.0
    elif _r3_mode == "OFF":
        if _r2_level <= cfg.low:
            _r3_mode = "ON"
            _r4_runtime = 0.0
    if _r3_mode == "ON":
        _r1_inflow = cfg.pump_rate
    else:
        _r1_inflow = 0.0
    _r2_level = _r2_level + dt * (_r1_inflow - demand)
    if _r2_level < 0.0:
        _r2_level = 0.0

    # --- Safety interlocks (latching) ---
    if _r2_level >= cfg.overflow:
        _r3_mode = "FAULT"
    if _r3_mode == "ON":
        if _r4_runtime > cfg.max_runtime:
            _r3_mode = "FAULT"

    if _r2_level > cfg.overflow:
        _r2_level = cfg.overflow
    _r0__spt_r1 = PumpState(_r2_level, _r3_mode, _r4_runtime)

    return _r0__spt_r1


def run_scenario() -> list:
    """Deterministic demand profile. Returns [(level, mode)] samples."""
    _r2_cfg = PumpConfig(low=20.0, high=80.0, overflow=100.0,
                     pump_rate=12.0, max_runtime=40.0)
    _r7_state = PumpState(level=50.0, mode="OFF", runtime=0.0)
    _r4_dt = 0.5

    _r6_samples = []
    _r0__lv0 = 0
    while _r0__lv0 < 400:
        _r8_step = _r0__lv0
        # Demand rises and falls in phases; a heavy-draw phase stresses the pump.
        _r5_phase = _r8_step % 200
        if _r5_phase < 60:
            _r3_demand = 4.0
        elif _r5_phase < 120:
            _r3_demand = 10.5
        else:
            _r3_demand = 2.0
        _r7_state = pump_step(_r7_state, _r3_demand, _r2_cfg, _r4_dt)
        _r1__t0 = (_r7_state.level, _r7_state.mode)
        _r6_samples.append(_r1__t0)
        _r0__lv0 += 1
    return _r6_samples


if __name__ == "__main__":
    out = run_scenario()
    _t1 = f"steps={len(out)}"
    print(_t1)
    _t2 = f"final level={out[-1][0]:.3f}  mode={out[-1][1]}"
    print(_t2)
    _t3 = sorted({m for _, m in out})
    print("modes seen:", _t3)
