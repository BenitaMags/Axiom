# CHOSEN-TARGET: first-party
"""controller -- PID pump controller regulating tank level to a setpoint.

The dependency on simple-pid has been removed. The PID computation is now
first-party code implemented directly in this module. The surrounding logic
(setpoint ramping, output clamping, AUTO/MANUAL mode) remains first-party.
The PID object is dependency-injected for testing.
"""
import time

from io_map import speed_to_counts


class _SimplePID:
    """Minimal first-party re-implementation of the subset of simple-pid's PID
    behaviour used by this program: proportional/integral/derivative control
    with a settable setpoint attribute, output clamping via output_limits,
    and a callable interface `pid(input_)` returning the control output.
    """

    def __init__(self, Kp, Ki, Kd, setpoint=0.0, output_limits=(None, None)):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._integral = 0.0
        self._last_input = None
        self._last_time = None

    def _clamp(self, value):
        lo, hi = self.output_limits
        if value is None:
            return value
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def __call__(self, input_):
        now = time.monotonic()
        if self._last_time is None:
            dt = 0.0
        else:
            dt = now - self._last_time
            if dt <= 0:
                dt = 1e-16

        error = self.setpoint - input_

        proportional = self.Kp * error

        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral)

        if self._last_input is None:
            derivative = 0.0
        else:
            derivative = -self.Kd * (input_ - self._last_input) / dt if dt else 0.0

        output = proportional + self._integral + derivative
        output = self._clamp(output)

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
