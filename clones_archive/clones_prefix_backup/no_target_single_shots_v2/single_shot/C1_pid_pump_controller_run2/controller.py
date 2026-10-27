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
    """Minimal first-party re-implementation of the subset of `simple_pid.PID`
    behaviour that this program relies on:

    - construction with (Kp, Ki, Kd, setpoint=..., output_limits=(lo, hi))
    - mutable `.setpoint` attribute
    - callable instance: `pid(input_)` returns the clamped control output
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

    def __call__(self, input_):
        now = time.monotonic()

        if self._last_time is None:
            dt = 0.0
        else:
            dt = now - self._last_time
            if dt <= 0.0:
                dt = 1e-16

        error = self.setpoint - input_

        self._integral += self.Ki * error * dt
        lo, hi = self.output_limits
        if hi is not None:
            self._integral = min(self._integral, hi)
        if lo is not None:
            self._integral = max(self._integral, lo)

        if self._last_input is None or dt == 0.0:
            derivative = 0.0
        else:
            derivative = -self.Kd * (input_ - self._last_input) / dt

        output = self.Kp * error + self._integral + derivative

        if hi is not None:
            output = min(output, hi)
        if lo is not None:
            output = max(output, lo)

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
