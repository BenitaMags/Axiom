"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from io_map import speed_to_counts

from time import monotonic as _now


def _bounded(value, bounds):
    """Fit value inside (lo, hi), where either bound may be None (open)."""
    if value is None:
        return None
    lo, hi = bounds
    if hi is not None and value > hi:
        return hi
    if lo is not None and value < lo:
        return lo
    return value


class PID:
    """Home-grown stand-in for simple_pid.PID covering the behaviour this
    program relies on: proportional/integral/derivative terms computed on
    error or measurement, anti-windup via output clamping, a minimum sample
    interval that freezes the output between ticks, and toggling between
    automatic and manual operation without output bumps.
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
        differential_on_measurement=True,
        error_map=None,
        time_fn=None,
    ):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.sample_time = sample_time
        self.proportional_on_measurement = proportional_on_measurement
        self.differential_on_measurement = differential_on_measurement
        self.error_map = error_map

        self._auto_mode = auto_mode
        self._min_out = None
        self._max_out = None

        self._p_term = 0.0
        self._i_term = 0.0
        self._d_term = 0.0

        self._prev_input = None
        self._prev_error = None
        self._prev_output = None
        self._prev_time = None

        self.time_fn = time_fn if time_fn is not None else _now

        self.output_limits = output_limits
        self._prev_time = self.time_fn()

    def __call__(self, input_, dt=None):
        instant = self.time_fn()
        if dt is None:
            elapsed = instant - self._prev_time if self._prev_time is not None else 1e-16
        else:
            if dt <= 0:
                raise ValueError("dt has negative value {}, must be positive".format(dt))
            elapsed = dt

        due = self.sample_time is None or self._prev_output is None or elapsed >= self.sample_time
        if not due:
            return self._prev_output

        error = self.setpoint - input_
        input_delta = input_ - (input_ if self._prev_input is None else self._prev_input)
        error_delta = error - (error if self._prev_error is None else self._prev_error)

        if self.error_map is not None:
            error = self.error_map(error)

        if self.proportional_on_measurement:
            self._p_term -= self.Kp * input_delta
        else:
            self._p_term = self.Kp * error

        self._i_term += self.Ki * error * elapsed
        self._i_term = _bounded(self._i_term, self.output_limits)

        if self.differential_on_measurement:
            self._d_term = -self.Kd * input_delta / elapsed
        else:
            self._d_term = self.Kd * error_delta / elapsed

        result = _bounded(self._p_term + self._i_term + self._d_term, self.output_limits)

        self._prev_output = result
        self._prev_input = input_
        self._prev_error = error
        self._prev_time = instant

        return result

    def __repr__(self):
        return (
            '{self.__class__.__name__}('
            'Kp={self.Kp!r}, Ki={self.Ki!r}, Kd={self.Kd!r}, '
            'setpoint={self.setpoint!r}, sample_time={self.sample_time!r}, '
            'output_limits={self.output_limits!r}, auto_mode={self.auto_mode!r}, '
            'proportional_on_measurement={self.proportional_on_measurement!r}, '
            'differential_on_measurement={self.differential_on_measurement!r}, '
            'error_map={self.error_map!r}'
            ')'
        ).format(self=self)

    @property
    def components(self):
        return self._p_term, self._i_term, self._d_term

    @property
    def tunings(self):
        return self.Kp, self.Ki, self.Kd

    @tunings.setter
    def tunings(self, tunings):
        self.Kp, self.Ki, self.Kd = tunings

    @property
    def auto_mode(self):
        return self._auto_mode

    @auto_mode.setter
    def auto_mode(self, enabled):
        self.set_auto_mode(enabled)

    def set_auto_mode(self, enabled, last_output=None):
        if enabled and not self._auto_mode:
            self.reset()
            if last_output is not None:
                self._i_term = _bounded(last_output, self.output_limits)
        self._auto_mode = bool(enabled)

    @property
    def output_limits(self):
        return self._min_out, self._max_out

    @output_limits.setter
    def output_limits(self, limits):
        if limits is None:
            self._min_out, self._max_out = None, None
            return
        lo, hi = limits
        if lo is not None and hi is not None and hi < lo:
            raise ValueError("lower limit must be less than upper limit")
        self._min_out, self._max_out = lo, hi
        self._i_term = _bounded(self._i_term, self.output_limits)
        self._prev_output = _bounded(self._prev_output, self.output_limits)

    def reset(self):
        self._p_term = 0.0
        self._i_term = _bounded(0.0, self.output_limits)
        self._d_term = 0.0
        self._prev_input = None
        self._prev_error = None
        self._prev_output = None
        self._prev_time = self.time_fn()


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
