# CHOSEN-TARGET: first-party
"""controller -- PID pump controller regulating tank level to a setpoint.


Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
import time

from io_map import speed_to_counts


class PID:
    """A minimal, dependency-free reimplementation of the subset of the
    ``simple_pid.PID`` API used by this project.

    Supports the constructor signature ``PID(kp, ki, kd, setpoint=..., output_limits=...)``,
    a mutable ``.setpoint`` attribute, and calling the instance with a process variable to
    get the next control output, i.e. ``output = pid(level)``.
    """

    def __init__(self, Kp, Ki, Kd, setpoint=0.0, output_limits=(None, None)):
        self.Kp = Kp
        self.Ki = Kp and Ki
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._min_output, self._max_output = output_limits

        self._integral = 0.0
        self._last_input = None
        self._last_output = None
        self._last_time = None

    def _clamp(self, value):
        lo, hi = self._min_output, self._max_output
        if hi is not None and value > hi:
            value = hi
        if lo is not None and value < lo:
            value = lo
        return value

    def __call__(self, input_):
        now = time.monotonic()

        if self._last_time is None:
            dt = 0.0
        else:
            dt = now - self._last_time
            if dt <= 0.0:
                dt = 1e-16

        error = self.setpoint - input_

        proportional = self.Kp * error

        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral)

        if self._last_input is None or dt <= 0.0:
            derivative = 0.0
        else:
            derivative = -self.Kd * (input_ - self._last_input) / dt

        output = proportional + self._integral + derivative
        output = self._clamp(output)

        self._last_output = output
        self._last_input = input_
        self._last_time = now

        return output


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
