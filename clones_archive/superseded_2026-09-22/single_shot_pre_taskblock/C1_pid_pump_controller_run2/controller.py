# CHOSEN-TARGET: first-party
"""controller -- PID pump controller regulating tank level to a setpoint.


Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts


class _SimplePID:
    """First-party re-implementation of the subset of `simple_pid.PID` behavior used
    here: a standard position-form PID controller with configurable output clamping,
    a mutable `.setpoint` attribute, and call-as-update semantics (`pid(input_)`).

    This mirrors the default behavior of simple-pid's PID class:
      - error = setpoint - input_
      - proportional term: Kp * error
      - integral term: accumulated Ki * error * dt, clamped to output_limits to
        avoid windup
      - derivative term: computed on measurement (derivative-on-measurement),
        i.e. -Kd * (input_ - last_input) / dt, matching simple-pid's default
        `proportional_on_measurement=False` mode
      - output clamped to output_limits
      - dt defaults to a nominal 1.0 time unit per call when no explicit timer
        is used (matching typical usage in this codebase where each call
        represents one discrete control step)
    """

    def __init__(self, kp, ki, kd, setpoint=0.0, output_limits=(None, None)):
        self.Kp = kp
        self.Ki = ki
        self.Kd = kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._last_input = None
        self._integral = 0.0
        self._last_output = 0.0

    def _clamp(self, value):
        lo, hi = self.output_limits
        if value is None:
            return value
        if lo is not None and value < lo:
            value = lo
        if hi is not None and value > hi:
            value = hi
        return value

    def __call__(self, input_, dt=1.0):
        error = self.setpoint - input_

        proportional = self.Kp * error

        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral)

        if self._last_input is None:
            derivative = 0.0
        else:
            derivative = -self.Kd * (input_ - self._last_input) / dt

        output = proportional + self._integral + derivative
        output = self._clamp(output)

        self._last_input = input_
        self._last_output = output

        return output


def _make_pid(kp, ki, kd, setpoint):
    return _SimplePID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))


class PumpController:
    def __init__(self, setpoint, kp=2.0, ki=0.5, kd=0.0, ramp=0.5, pid=None):
        self.target = setpoint
        self.applied_setpoint = 0.0
        self.ramp = ramp
        self.mode = "AUTO"
        self.pid = pid if pid is not None else _make_pid(kp, ki, kd, 0.0)

    def _ramp_setpoint(self):
        """First-party logic: move the applied setpoint toward the target by <= ramp."""
        if self.applied_setpoint < self.target:
            self.applied_setpoint = min(self.target, self.applied_setpoint + self.ramp)
        elif self.applied_setpoint > self.target:
            self.applied_setpoint = max(self.target, self.applied_setpoint - self.ramp)
        self.pid.setpoint = self.applied_setpoint

    def update(self, level) -> int:
        """Compute the pump-speed command (in DAC counts) for the measured level."""
        if self.mode == "MANUAL":
            return speed_to_counts(0.0)
        self._ramp_setpoint()
        speed = self.pid(level)
        speed = max(0.0, min(100.0, speed))  # clamp before actuation
        return speed_to_counts(speed)
