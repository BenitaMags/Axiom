"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts
import time


def _clamp(value, limits):
    lower, upper = limits
    if value is None:
        return None
    if upper is not None and value > upper:
        value = upper
    if lower is not None and value < lower:
        value = lower
    return value


class _SimplePID:
    """Minimal first-party re-implementation of simple-pid's PID behaviour."""

    def __init__(self, Kp=1.0, Ki=0.0, Kd=0.0, setpoint=0.0, output_limits=(None, None)):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._integral = 0.0
        self._last_input = None
        self._last_output = None
        self._last_time = None

    def __call__(self, input_):
        now = time.monotonic()
        if self._last_time is None:
            dt = 1.0
        else:
            dt = now - self._last_time
            if dt <= 0.0:
                dt = 1e-16

        error = self.setpoint - input_

        proportional = self.Kp * error

        self._integral += self.Ki * error * dt
        self._integral = _clamp(self._integral, self.output_limits)

        if self._last_input is None:
            d_input = 0.0
        else:
            d_input = input_ - self._last_input
        derivative = -self.Kd * d_input / dt

        output = proportional + self._integral + derivative
        output = _clamp(output, self.output_limits)

        self._last_output = output
        self._last_input = input_
        self._last_time = now

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
