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
    _r5_error = setpoint - state.rpm
    _r0__t0 = state.integral + _r5_error * dt

    # Integral term with symmetric anti-windup clamp.
    _r6_integral = clamp(_r0__t0, -2500.0, 2500.0)

    # Derivative on the error signal.
    _r4_derivative = (_r5_error - state.prev_error) / dt

    _r3_command = gains.kp * _r5_error + gains.ki * _r6_integral + gains.kd * _r4_derivative
    _r3_command = clamp(_r3_command, 0.0, 100.0)  # fuel valve is 0-100 %

    # First-order engine: fuel raises RPM, friction + load remove it.
    _r1_a = 60.0   # fuel-to-RPM gain
    _r2_b = 1.0    # friction coefficient
    _r7_rpm_next = state.rpm + dt * (_r1_a * _r3_command - _r2_b * state.rpm - load)

    return GovernorState(_r7_rpm_next, _r6_integral, _r5_error), _r3_command


def run_scenario() -> list:
    """Deterministic warm-up-then-load-step scenario. Returns [(rpm, command)]."""
    _r2_gains = PIDGains(kp=0.015, ki=0.040, kd=0.0008)
    _r6_state = GovernorState(rpm=0.0, integral=0.0, prev_error=0.0)
    _r5_setpoint = 3000.0
    _r1_dt = 0.05

    _r4_samples = []
    for step in range(600):
        # A load is applied partway through, then partly removed later.
        if step < 200:
            _r3_load = 0.0
        elif step < 400:
            _r3_load = 900.0
        else:
            _r3_load = 300.0
        _r6_state, command = governor_step(_r6_state, _r5_setpoint, _r2_gains, _r3_load, _r1_dt)
        _r0__t2 = (_r6_state.rpm, command)
        _r4_samples.append(_r0__t2)
    return _r4_samples


if __name__ == "__main__":
    out = run_scenario()
    _t3 = f"steps={len(out)}"
    print(_t3)
    _t4 = f"final rpm={out[-1][0]:.3f}  command={out[-1][1]:.3f}"
    print(_t4)
