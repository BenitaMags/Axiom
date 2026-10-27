# CHOSEN-TARGET: first-party
"""controller -- PID pump controller regulating tank level to a setpoint.

The PID computation is first-party code (see `pid.py`), replacing the former
`simple-pid` dependency. The surrounding logic (setpoint ramping, output
clamping, AUTO/MANUAL mode) is first-party as well. The PID object is
dependency-injected for testing.
"""
from io_map import speed_to_counts
from pid import PID

_OUTPUT_LOW = 0.0
_OUTPUT_HIGH = 100.0


def _build_pid(kp, ki, kd, setpoint):
    return PID(kp, ki, kd, setpoint=setpoint,
               output_limits=(_OUTPUT_LOW, _OUTPUT_HIGH))


def _clamp_output(value):
    return max(_OUTPUT_LOW, min(_OUTPUT_HIGH, value))


class PumpController:
    MODE_AUTO = "AUTO"
    MODE_MANUAL = "MANUAL"

    def __init__(self, setpoint, kp=2.0, ki=0.5, kd=0.0, ramp=0.5, pid=None):
        self.target = setpoint
        self.applied_setpoint = 0.0
        self.ramp = ramp
        self.mode = self.MODE_AUTO
        self.pid = pid if pid is not None else _build_pid(kp, ki, kd, 0.0)

    def _advance_setpoint(self):
        """Move the applied setpoint toward the target by at most `ramp`."""
        gap = self.target - self.applied_setpoint
        if gap > 0:
            step = min(gap, self.ramp)
        elif gap < 0:
            step = max(gap, -self.ramp)
        else:
            step = 0.0
        self.applied_setpoint += step
        self.pid.setpoint = self.applied_setpoint

    def _compute_speed(self, level):
        self._advance_setpoint()
        raw_speed = self.pid(level)
        return _clamp_output(raw_speed)

    def update(self, level) -> int:
        """Compute the pump-speed command (in DAC counts) for the measured level."""
        if self.mode == self.MODE_MANUAL:
            speed = 0.0
        else:
            speed = self._compute_speed(level)
        return speed_to_counts(speed)
