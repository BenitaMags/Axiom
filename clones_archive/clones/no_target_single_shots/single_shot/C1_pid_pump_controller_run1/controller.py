"""controller -- PID pump controller regulating tank level to a setpoint.

The PID computation is implemented first-party below (no external dependency),
while the surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode)
remains first-party as before. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts


class _MiniPID:
    """A compact, first-party PID controller replacement.

    Mimics the subset of `simple_pid.PID` behavior used by this program:
    - constructed with (kp, ki, kd, setpoint=..., output_limits=(lo, hi))
    - callable with the current measured value, returning the clamped output
    - `.setpoint` attribute can be read/written directly
    """

    def __init__(self, k_p, k_i, k_d, setpoint=0.0, output_limits=(None, None)):
        self._gains = (k_p, k_i, k_d)
        self.setpoint = setpoint
        self._out_lo, self._out_hi = output_limits
        self._accumulator = 0.0
        self._previous_measurement = None

    def _clamp(self, value):
        lo, hi = self._out_lo, self._out_hi
        if lo is not None and value < lo:
            value = lo
        if hi is not None and value > hi:
            value = hi
        return value

    def __call__(self, measurement):
        kp, ki, kd = self._gains
        error = self.setpoint - measurement

        proportional_term = kp * error

        self._accumulator += ki * error
        self._accumulator = self._clamp(self._accumulator)

        if self._previous_measurement is None:
            derivative_term = 0.0
        else:
            delta_measurement = measurement - self._previous_measurement
            derivative_term = -kd * delta_measurement

        self._previous_measurement = measurement

        raw_output = proportional_term + self._accumulator + derivative_term
        return self._clamp(raw_output)


def _make_pid(kp, ki, kd, setpoint):
    return _MiniPID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))


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
