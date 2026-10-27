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
    def __init__(
        self,
        Kp=1.0,
        Ki=0.0,
        Kd=0.0,
        setpoint=0,
        sample_time=0.01,
        output_limits=(None, None),
    ):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.sample_time = sample_time

        self._min_output, self._max_output = None, None
        self._proportional = 0.0
        self._integral = 0.0
        self._derivative = 0.0

        self._last_time = None
        self._last_output = None
        self._last_input = None

        self.output_limits = output_limits

    def __call__(self, input_, dt=None):
        now = time.monotonic()
        if dt is None:
            if not (self._last_time is not None):
                dt = 1e-16
            else:
                dt = now - self._last_time
        elif dt <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        if (
            self.sample_time is not None
            and dt < self.sample_time
            and self._last_output is not None
        ):
            _spt_r1 = self._last_output
            return _spt_r1

        error = self.setpoint - input_
        d_input = input_ - (self._last_input if self._last_input is not None else input_)

        self._proportional = self.Kp * error

        self._integral = self._integral + self.Ki * error * dt
        self._integral = self._clamp(self._integral, self.output_limits)
        _spt_t1 = -self.Kd * d_input

        self._derivative = _spt_t1 / dt
        _spt_t2 = self._proportional + self._integral

        output = _spt_t2 + self._derivative
        output = self._clamp(output, self.output_limits)

        self._last_output = output
        self._last_input = input_
        self._last_time = now

        return output

    def __repr__(self):
        _spt_r2 = (
            "{self.__class__.__name__}("
            "Kp={self.Kp!r}, Ki={self.Ki!r}, Kd={self.Kd!r}, "
            "setpoint={self.setpoint!r}, sample_time={self.sample_time!r}, "
            "output_limits={self.output_limits!r}"
            ")"
        ).format(self=self)
        return _spt_r2

    @property
    def components(self):
        _spt_r3 = self._proportional, self._integral, self._derivative
        return _spt_r3

    @property
    def tunings(self):
        _spt_r4 = self.Kp, self.Ki, self.Kd
        return _spt_r4

    @tunings.setter
    def tunings(self, tunings):
        self.Kp, self.Ki, self.Kd = tunings

    @property
    def output_limits(self):
        _spt_r5 = self._min_output, self._max_output
        return _spt_r5

    @output_limits.setter
    def output_limits(self, limits):
        if limits is None:
            self._min_output, self._max_output = None, None
            return

        min_output, max_output = limits

        if None not in limits and max_output < min_output:
            raise ValueError("lower limit must be less than upper limit")

        self._min_output = min_output
        self._max_output = max_output

        self._integral = self._clamp(self._integral, self.output_limits)
        self._last_output = self._clamp(self._last_output, self.output_limits)

    def _clamp(self, value, limits):
        lower, upper = limits
        if value is None:
            return None
        elif upper is not None and value > upper:
            return upper
        elif lower is not None and value < lower:
            return lower
        return value

    def reset(self):
        self._proportional = 0
        self._integral = 0
        self._derivative = 0

        self._integral = self._clamp(self._integral, self.output_limits)

        self._last_time = None
        self._last_output = None
        self._last_input = None


def _make_pid(kp, ki, kd, setpoint):
    _spt_r6 = PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))
    return _spt_r6


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
            _spt_r7 = speed_to_counts(0.0)
            return _spt_r7
        self._ramp_setpoint()
        speed = self.pid(level)
        speed = max(0.0, min(100.0, speed))  # clamp before actuation
        _spt_r8 = speed_to_counts(speed)
        return _spt_r8
