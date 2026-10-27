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
    _r9_error = setpoint - state.rpm
    _r1__spt_t1 = state.integral + _r9_error * dt

    # Integral term with symmetric anti-windup clamp.
    _r10_integral = clamp(_r1__spt_t1, -2500.0, 2500.0)
    _r2__spt_t2 = (_r9_error - state.prev_error)

    # Derivative on the error signal.
    _r8_derivative = _r2__spt_t2 / dt
    _r4__spt_t4 = gains.kp * _r9_error
    _r3__spt_t3 = _r4__spt_t4 + gains.ki * _r10_integral

    _r7_command = _r3__spt_t3 + gains.kd * _r8_derivative
    _r7_command = clamp(_r7_command, 0.0, 100.0)  # fuel valve is 0-100 %

    # First-order engine: fuel raises RPM, friction + load remove it.
    _r5_a = 60.0   # fuel-to-RPM gain
    _r6_b = 1.0    # friction coefficient
    _r11_rpm_next = state.rpm + dt * (_r5_a * _r7_command - _r6_b * state.rpm - load)
    _r0__spt_r1 = GovernorState(_r11_rpm_next, _r10_integral, _r9_error), _r7_command

    return _r0__spt_r1


def run_scenario() -> list:
    """Deterministic warm-up-then-load-step scenario. Returns [(rpm, command)]."""
    _r3_gains = PIDGains(kp=0.015, ki=0.040, kd=0.0008)
    _r7_state = GovernorState(rpm=0.0, integral=0.0, prev_error=0.0)
    _r6_setpoint = 3000.0
    _r2_dt = 0.05

    _r5_samples = []
    _r0__lv0 = 0
    while _r0__lv0 < 600:
        _r8_step = _r0__lv0
        # A load is applied partway through, then partly removed later.
        if _r8_step < 200:
            _r4_load = 0.0
        elif _r8_step < 400:
            _r4_load = 900.0
        else:
            _r4_load = 300.0
        _r7_state, command = governor_step(_r7_state, _r6_setpoint, _r3_gains, _r4_load, _r2_dt)
        _r1__t1 = (_r7_state.rpm, command)
        _r5_samples.append(_r1__t1)
        _r0__lv0 += 1
    return _r5_samples


if __name__ == "__main__":
    out = run_scenario()
    _t2 = f"steps={len(out)}"
    print(_t2)
    _t3 = f"final rpm={out[-1][0]:.3f}  command={out[-1][1]:.3f}"
    print(_t3)
