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
    """Minimal first-party re-implementation of a simple_pid.PID-like controller.

    Supports the subset of behavior used by PumpController:
      - construction with (kp, ki, kd, setpoint=..., output_limits=(lo, hi))
      - a mutable `.setpoint` attribute
      - calling the instance with a process variable to get the control output
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
        self._last_output = None

    def _clamp_output(self, value):
        lo, hi = self.output_limits
        if value is None:
            return value
        if hi is not None and value > hi:
            value = hi
        if lo is not None and value < lo:
            value = lo
        return value

    def _clamp_integral(self, integral):
        lo, hi = self.output_limits
        if hi is not None and integral > hi:
            integral = hi
        if lo is not None and integral < lo:
            integral = lo
        return integral

    def __call__(self, input_, dt=None):
        now = time.monotonic()
        if dt is None:
            dt = now - self._last_time if (self._last_time is not None) else 1e-16
            dt = max(1e-16, dt)

        error = self.setpoint - input_

        d_input = input_ - (self._last_input if self._last_input is not None else input_)

        self._integral += self.Ki * error * dt
        self._integral = self._clamp_integral(self._integral)

        p_term = self.Kp * error
        i_term = self._integral
        d_term = -self.Kd * d_input / dt

        output = p_term + i_term + d_term
        output = self._clamp_output(output)

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
