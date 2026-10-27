# CHOSEN-TARGET: first-party
"""controller -- PID pump controller regulating tank level to a setpoint.


Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts


class _SimplePID:
    """Minimal first-party re-implementation of the subset of simple-pid's PID
    behavior used by this module: proportional-integral-derivative control with
    a settable ``setpoint`` attribute, callable interface, and output clamping
    via ``output_limits``.
    """

    def __init__(self, kp, ki, kd, setpoint=0.0, output_limits=(None, None)):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._integral = 0.0
        self._last_input = None
        self._last_output = None

    def __call__(self, input_value):
        error = self.setpoint - input_value

        self._integral += self.ki * error

        lo, hi = self.output_limits
        if lo is not None:
            self._integral = max(lo, self._integral)
        if hi is not None:
            self._integral = min(hi, self._integral)

        if self._last_input is None:
            derivative = 0.0
        else:
            derivative = -(input_value - self._last_input)

        output = self.kp * error + self._integral + self.kd * derivative

        if lo is not None:
            output = max(lo, output)
        if hi is not None:
            output = min(hi, output)

        self._last_input = input_value
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
