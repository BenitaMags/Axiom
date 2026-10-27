"""Engine RPM governor -- PID feedback control loop.

Models a simplified engine whose crankshaft speed (RPM) is regulated toward a
target by a PID controller acting on a fuel/throttle command (0-100 %). A
first-order plant turns fuel into speed; friction bleeds speed off. Load
disturbances are injected on a fixed schedule.

Fully deterministic: no wall-clock, no external RNG state. `run_scenario()`
returns a fixed list of samples so a diversified variant can be checked for
exact (or tolerance-based) equivalence.

Control-flow of interest for a diversifier:
  * anti-windup clamp (nested min/max branching)
  * per-step feedback with state carried across iterations
  * float arithmetic (reassociation is NOT bit-exact -- see README)
"""

from dataclasses import dataclass


@dataclass
class PIDGains:
    kp: float
    ki: float
    kd: float


@dataclass
class GovernorState:
    rpm: float
    integral: float
    prev_error: float


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def governor_step(state: GovernorState, setpoint: float,
                  gains: PIDGains, load: float, dt: float) -> tuple:
    """Advance controller + plant by one step. Returns (new_state, command)."""
    _r3_error = setpoint - state.rpm

    # Integral term with symmetric anti-windup clamp.
    _r4_integral = clamp(state.integral + _r3_error * dt, -2500.0, 2500.0)

    # Derivative on the error signal.
    _r2_derivative = (_r3_error - state.prev_error) / dt

    _r1_command = gains.kp * _r3_error + gains.ki * _r4_integral + gains.kd * _r2_derivative
    _r1_command = clamp(_r1_command, 0.0, 100.0)  # fuel valve is 0-100 %

    # First-order engine: fuel raises RPM, friction + load remove it.
    _r0_a = 60.0   # fuel-to-RPM gain

    return GovernorState((state.rpm + dt * (_r0_a * _r1_command - 1.0 * state.rpm - load)), _r4_integral, _r3_error), _r1_command


def run_scenario() -> list:
    """Deterministic warm-up-then-load-step scenario. Returns [(rpm, command)]."""
    _r1_gains = PIDGains(kp=0.015, ki=0.040, kd=0.0008)
    _r5_state = GovernorState(rpm=0.0, integral=0.0, prev_error=0.0)
    _r4_setpoint = 3000.0
    _r0_dt = 0.05

    _r3_samples = []
    for step in range(600):
        # A load is applied partway through, then partly removed later.
        if step < 200:
            _r2_load = 0.0
        elif step < 400:
            _r2_load = 900.0
        else:
            _r2_load = 300.0
        _r5_state, command = governor_step(_r5_state, _r4_setpoint, _r1_gains, _r2_load, _r0_dt)
        _r3_samples.append((_r5_state.rpm, command))
    return _r3_samples


if __name__ == "__main__":
    out = run_scenario()
    print(f"steps={len(out)}")
    print(f"final rpm={out[-1][0]:.3f}  command={out[-1][1]:.3f}")
