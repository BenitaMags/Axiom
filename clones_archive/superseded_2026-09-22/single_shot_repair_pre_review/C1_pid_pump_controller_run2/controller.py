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
      - a `sample_time` (default 0.01s) below which repeated calls return the
        previously computed output unchanged (matching simple_pid's default
        rate-limiting behavior), rather than recomputing on every call.
    """

    def __init__(self, Kp, Ki, Kd, setpoint=0.0, output_limits=(None, None), sample_time=0.01):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits
        self.sample_time = sample_time

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

        if dt is not None:
            update_dt = dt
        else:
            if self._last_time is None:
                update_dt = 1e-16
            else:
                update_dt = now - self._last_time
                if self.sample_time is not None and update_dt < self.sample_time and self._last_output is not None:
                    # Not enough time has elapsed: hold the previous output,
                    # matching simple_pid's sample_time gating behavior.
                    return self._last_output
                update_dt = max(1e-16, update_dt)

        error = self.setpoint - input_

        d_input = input_ - (self._last_input if self._last_input is not None else input_)

        self._integral += self.Ki * error * update_dt
        self._integral = self._clamp_integral(self._integral)

        p_term = self.Kp * error
        i_term = self._integral
        d_term = -self.Kd * d_input / update_dt

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
