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
    error = setpoint - state.rpm
    _t0 = state.integral + error * dt

    # Integral term with symmetric anti-windup clamp.
    integral = clamp(_t0, -2500.0, 2500.0)
    _spt_t1 = (error - state.prev_error)

    # Derivative on the error signal.
    derivative = _spt_t1 / dt
    _spt_t3 = gains.kp * error
    _spt_t2 = _spt_t3 + gains.ki * integral

    command = _spt_t2 + gains.kd * derivative
    command = clamp(command, 0.0, 100.0)  # fuel valve is 0-100 %

    # First-order engine: fuel raises RPM, friction + load remove it.
    a = 60.0   # fuel-to-RPM gain
    b = 1.0    # friction coefficient
    rpm_next = state.rpm + dt * (a * command - b * state.rpm - load)
    _spt_r1 = GovernorState(rpm_next, integral, error), command

    return _spt_r1


def run_scenario() -> list:
    """Deterministic warm-up-then-load-step scenario. Returns [(rpm, command)]."""
    gains = PIDGains(kp=0.015, ki=0.040, kd=0.0008)
    state = GovernorState(rpm=0.0, integral=0.0, prev_error=0.0)
    setpoint = 3000.0
    dt = 0.05

    samples = []
    _lv0 = 0
    while _lv0 < 600:
        step = _lv0
        # A load is applied partway through, then partly removed later.
        if step < 200:
            load = 0.0
        elif step >= 400:
            load = 300.0
        else:
            load = 900.0
        state, command = governor_step(state, setpoint, gains, load, dt)
        _t2 = (state.rpm, command)
        samples.append(_t2)
        _lv0 += 1
    return samples


if __name__ == "__main__":
    out = run_scenario()
    _t3 = f"steps={len(out)}"
    print(_t3)
    _t4 = f"final rpm={out[-1][0]:.3f}  command={out[-1][1]:.3f}"
    print(_t4)
