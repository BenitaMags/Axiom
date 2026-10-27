"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts

import time


def _clamp(value, limits):
    lower, upper = limits
    if value is None:
        return None
    if upper is not None:
        if value > upper:
            return upper
    if lower is not None:
        if value < lower:
            return lower
    return value


class PID:
    """First-party re-implementation of the `simple-pid` PID controller.

    Mirrors the public API and observable behaviour of `simple_pid.PID`:
    construction arguments, output clamping, integral windup limiting,
    derivative-on-measurement, and sample-time based rate limiting/caching.
    """

    def __init__(
        self,
        Kp=1.0,
        Ki=0.0,
        Kd=0.0,
        setpoint=0,
        sample_time=0.01,
        output_limits=(None, None),
        auto_mode=True,
        proportional_on_measurement=False,
        error_map=None,
    ):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.sample_time = sample_time
        self.proportional_on_measurement = proportional_on_measurement
        self.error_map = error_map

        self._min_output, self._max_output = None, None
        self._auto_mode = auto_mode

        self._proportional = 0
        self._integral = 0
        self._derivative = 0

        self._last_time = None
        self._last_output = None
        self._last_error = None
        self._last_input = None

        self.output_limits = output_limits

        self._last_time = time.monotonic()

    def __call__(self, input_, dt=None):
        if not self.auto_mode:
            _spt_r1 = self._last_output
            return _spt_r1

        now = time.monotonic()
        if dt is None:
            dt = 1e-16 if not (now - self._last_time) else now - self._last_time
        elif dt <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        if self.sample_time is not None:
            if dt < self.sample_time:
                if self._last_output is not None:
                    _spt_r2 = self._last_output
                    return _spt_r2

        error = self.setpoint - input_
        d_input = input_ - (self._last_input if self._last_input is not None else input_)
        d_error = error - (self._last_error if self._last_error is not None else error)

        if self.error_map is not None:
            error = self.error_map(error)

        self._proportional = self.Kp * error
        if self.proportional_on_measurement:
            self._proportional = self._proportional - self.Kp * d_input

        self._integral = self._integral + self.Ki * error * dt
        self._integral = _clamp(self._integral, self.output_limits)
        if not (dt > 0):
            self._derivative = 0.0
        else:
            _spt_t1 = -self.Kd * d_input
            self._derivative = _spt_t1 / dt
        if not self.proportional_on_measurement:
            pass
        _spt_t2 = self._proportional + self._integral

        output = _spt_t2 + self._derivative
        output = _clamp(output, self.output_limits)

        self._last_output = output
        self._last_input = input_
        self._last_error = error
        self._last_time = now

        return output

    def __repr__(self):
        _spt_r3 = (
            "{self.__class__.__name__}("
            "Kp={self.Kp!r}, Ki={self.Ki!r}, Kd={self.Kd!r}, "
            "setpoint={self.setpoint!r}, sample_time={self.sample_time!r}, "
            "output_limits={self.output_limits!r}, auto_mode={self.auto_mode!r}, "
            "proportional_on_measurement={self.proportional_on_measurement!r},"
            ")"
        ).format(self=self)
        return _spt_r3

    @property
    def components(self):
        _spt_r4 = self._proportional, self._integral, self._derivative
        return _spt_r4

    @property
    def tunings(self):
        _spt_r5 = self.Kp, self.Ki, self.Kd
        return _spt_r5

    @tunings.setter
    def tunings(self, tunings):
        self.Kp, self.Ki, self.Kd = tunings

    @property
    def auto_mode(self):
        _spt_r6 = self._auto_mode
        return _spt_r6

    @auto_mode.setter
    def auto_mode(self, enabled):
        self.set_auto_mode(enabled)

    def set_auto_mode(self, enabled, last_output=None):
        if not (enabled and not self._auto_mode):
            self._auto_mode = enabled
        else:
            self._auto_mode = True
            self.reset()
            if last_output is not None:
                self._integral = last_output
                self._integral = _clamp(self._integral, self.output_limits)

    @property
    def output_limits(self):
        _spt_r7 = self._min_output, self._max_output
        return _spt_r7

    @output_limits.setter
    def output_limits(self, limits):
        if limits is None:
            self._min_output, self._max_output = None, None
            return

        min_output, max_output = limits

        if None not in (min_output, max_output):
            if max_output < min_output:
                raise ValueError("lower limit must be less than upper limit")

        self._min_output = min_output
        self._max_output = max_output

        self._integral = _clamp(self._integral, self.output_limits)
        self._last_output = _clamp(self._last_output, self.output_limits)

    def reset(self):
        self._proportional = 0
        self._integral = 0
        self._derivative = 0

        self._integral = _clamp(self._integral, self.output_limits)

        self._last_time = time.monotonic()
        self._last_output = None
        self._last_input = None
        self._last_error = None


def _make_pid(kp, ki, kd, setpoint):
    _spt_r8 = PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))
    return _spt_r8


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
            _spt_r9 = speed_to_counts(0.0)
            return _spt_r9
        self._ramp_setpoint()
        speed = self.pid(level)
        speed = max(0.0, min(100.0, speed))  # clamp before actuation
        _spt_r10 = speed_to_counts(speed)
        return _spt_r10
