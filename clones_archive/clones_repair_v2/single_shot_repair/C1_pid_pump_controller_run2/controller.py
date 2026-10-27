# CHOSEN-TARGET: first-party
"""controller -- PID pump controller regulating tank level to a setpoint.


Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
import time

from io_map import speed_to_counts


class _SimplePID:
    """First-party re-implementation of a basic PID controller.

    Mirrors the minimal subset of the `simple_pid.PID` API used by this module:
    construction with (kp, ki, kd, setpoint=..., output_limits=...), a mutable
    `setpoint` attribute, and a `__call__(input_)` method returning the clamped
    control output.
    """

    def __init__(self, kp, ki, kd, setpoint=0.0, output_limits=(None, None)):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._integral = 0.0
        self._last_input = None
        self._last_time = None

    def _clamp(self, value):
        lo, hi = self.output_limits
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def __call__(self, input_, dt=None):
        now = time.monotonic()
        if dt is None:
            if self._last_time is None:
                dt = 1.0
            else:
                dt = max(now - self._last_time, 1e-16)
        self._last_time = now

        error = self.setpoint - input_

        proportional = self.kp * error

        self._integral += self.ki * error * dt
        self._integral = self._clamp(self._integral)

        if self._last_input is None:
            derivative = 0.0
        else:
            derivative = -self.kd * (input_ - self._last_input) / dt

        self._last_input = input_

        output = proportional + self._integral + derivative
        output = self._clamp(output)
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
