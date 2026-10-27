"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts


def _make_pid(kp, ki, kd, setpoint):
    from simple_pid import PID  # lazy import for testability
    _spt_r1 = PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))

    return _spt_r1


class PumpController:
    def __init__(self, setpoint, kp=2.0, ki=0.5, kd=0.0, ramp=0.5, pid=None):
        self.target = setpoint
        self.applied_setpoint = 0.0
        self.ramp = ramp
        self.mode = "AUTO"
        if not (pid is not None):
            self.pid = _make_pid(kp, ki, kd, 0.0)
        else:
            self.pid = pid

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
            _spt_r2 = speed_to_counts(0.0)
            return _spt_r2
        self._ramp_setpoint()
        _spt_r3 = speed_to_counts(max(0.0, min(100.0, self.pid(level))))
        return _spt_r3
