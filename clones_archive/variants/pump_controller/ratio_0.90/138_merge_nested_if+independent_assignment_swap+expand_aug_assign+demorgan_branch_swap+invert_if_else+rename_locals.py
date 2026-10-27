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
    _r1_level = state.level
    _r2_mode = state.mode
    _r3_runtime = state.runtime

    # --- Hysteresis control (only while not faulted) ---
    if _r2_mode == "ON":
        _r3_runtime = _r3_runtime + dt
        if _r1_level >= cfg.high:
            _r3_runtime = 0.0
            _r2_mode = "OFF"
    elif _r2_mode == "OFF" and _r1_level <= cfg.low:
        _r3_runtime = 0.0
        _r2_mode = "ON"

    # --- Plant update ---
    _r0_inflow = cfg.pump_rate if _r2_mode == "ON" else 0.0
    _r1_level = _r1_level + dt * (_r0_inflow - demand)
    if _r1_level < 0.0:
        _r1_level = 0.0

    # --- Safety interlocks (latching) ---
    if _r1_level >= cfg.overflow:
        _r2_mode = "FAULT"
    if _r2_mode == "ON" and _r3_runtime > cfg.max_runtime:
        _r2_mode = "FAULT"

    if _r1_level > cfg.overflow:
        _r1_level = cfg.overflow

    return PumpState(_r1_level, _r2_mode, _r3_runtime)


def run_scenario() -> list:
    """Deterministic demand profile. Returns [(level, mode)] samples."""
    _r0_cfg = PumpConfig(low=20.0, high=80.0, overflow=100.0,
                     pump_rate=12.0, max_runtime=40.0)
    _r5_state = PumpState(level=50.0, mode="OFF", runtime=0.0)

    _r4_samples = []
    _r2_dt = 0.5
    for step in range(400):
        # Demand rises and falls in phases; a heavy-draw phase stresses the pump.
        _r3_phase = step % 200
        if _r3_phase < 60:
            _r1_demand = 4.0
        elif not (_r3_phase >= 120):
            _r1_demand = 10.5
        else:
            _r1_demand = 2.0
        _r5_state = pump_step(_r5_state, _r1_demand, _r0_cfg, _r2_dt)
        _r4_samples.append((_r5_state.level, _r5_state.mode))
    return _r4_samples


if __name__ == "__main__":
    out = run_scenario()
    print(f"steps={len(out)}")
    print(f"final level={out[-1][0]:.3f}  mode={out[-1][1]}")
    print("modes seen:", sorted({m for _, m in out}))
