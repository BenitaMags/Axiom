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
    """Minimal re-implementation of the subset of `simple_pid.PID` used here.

    Behaviour reproduced from simple-pid:
    - constructor signature (Kp, Ki, Kd, setpoint=0.0, output_limits=(None, None))
    - calling the instance with an input value computes the next output
    - proportional, integral and derivative terms computed from the error
    - integral term is clamped to output_limits to prevent windup
    - final output is clamped to output_limits
    - setpoint is a plain assignable attribute
    - time-based dt using monotonic clock, with a safe minimum on first call
    """

    def __init__(self, Kp=1.0, Ki=0.0, Kd=0.0, setpoint=0.0,
                 output_limits=(None, None)):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._last_time = None
        self._last_input = None
        self._last_output = None
        self._integral = 0.0

    def _clamp(self, value, limits):
        lower, upper = limits
        if value is None:
            return None
        if upper is not None and value > upper:
            value = upper
        if lower is not None and value < lower:
            value = lower
        return value

    def __call__(self, input_):
        now = time.monotonic()
        if self._last_time is None:
            dt = 1e-16
        else:
            dt = now - self._last_time
            if dt <= 0.0:
                dt = 1e-16

        error = self.setpoint - input_

        # Proportional term
        p_term = self.Kp * error

        # Integral term with anti-windup clamping
        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral, self.output_limits)

        # Derivative term (on measurement, matching simple-pid's default behaviour
        # is on error; simple-pid computes derivative on input by default only
        # when proportional_on_measurement is True -- default False means it's
        # derivative-on-error)
        if self._last_input is None:
            d_input = 0.0
        else:
            d_input = input_ - self._last_input
        if self._last_time is None:
            d_term = 0.0
        else:
            d_term = -self.Kd * (d_input / dt)

        output = p_term + self._integral + d_term
        output = self._clamp(output, self.output_limits)

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
