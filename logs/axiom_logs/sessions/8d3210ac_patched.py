"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
import time

from io_map import speed_to_counts


class PID:
    """Minimal re-implementation of the `simple-pid` PID controller API.

    Reproduces the behaviour of `simple_pid.PID` as used in this file:
    constructor signature `(Kp, Ki, Kd, setpoint=0.0, output_limits=(None, None))`,
    callable interface `pid(input_)` returning the clamped control output,
    an assignable `setpoint` attribute, and anti-windup clamping of the
    integral term to the configured output limits.
    """

    def __init__(
        self,
        Kp=1.0,
        Ki=0.0,
        Kd=0.0,
        setpoint=0.0,
        sample_time=0.01,
        output_limits=(None, None),
    ):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.sample_time = sample_time
        self._min_output, self._max_output = output_limits

        self._last_time = None
        self._last_input = None
        self._last_output = None
        self._proportional = 0.0
        self._integral = 0.0
        self._derivative = 0.0

    def _clamp(self, value):
        if value is None:
            return value
        lo, hi = self._min_output, self._max_output
        if hi is not None and value > hi:
            return hi
        if lo is not None and value < lo:
            return lo
        return value

    def __call__(self, input_, dt=None):
        now = time.monotonic()

        if dt is None:
            dt = now - self._last_time if (self._last_time is not None) else 1e-16
        elif dt <= 0:
            dt = 1e-16

        if (
            self.sample_time is not None
            and self._last_time is not None
            and dt < self.sample_time
            and self._last_output is not None
        ):
            return self._last_output

        error = self.setpoint - input_
        d_input = input_ - (self._last_input if self._last_input is not None else input_)

        self._proportional = self.Kp * error

        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral)

        self._derivative = -self.Kd * d_input / dt

        output = self._proportional + self._integral + self._derivative
        output = self._clamp(output)

        self._last_output = output
        self._last_input = input_
        self._last_time = now

        return output

    @property
    def output_limits(self):
        return self._min_output, self._max_output

    @output_limits.setter
    def output_limits(self, limits):
        self._min_output, self._max_output = limits


def _make_pid(kp, ki, kd, setpoint):
    return PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))


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
