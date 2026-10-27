"""pid_engine -- first-party reimplementation of simple_pid.PID's observable behaviour.

Extracted into its own module so the PID algorithm can be tested, tuned, and swapped
independently of the pump-control logic that consumes it.
"""
import time


def _clamp(value, limits):
    lower, upper = limits
    if value is None:
        return None
    if upper is not None and value > upper:
        return upper
    if lower is not None and value < lower:
        return lower
    return value


class _TimeSource:
    """Wraps the monotonic clock so its use reads as a single responsibility."""

    @staticmethod
    def now():
        return time.monotonic()


class _ErrorTerms:
    """Bundles the raw error/derivative computation for one PID step."""

    __slots__ = ("error", "d_input", "d_error")

    def __init__(self, error, d_input, d_error):
        self.error = error
        self.d_input = d_input
        self.d_error = d_error

    @classmethod
    def compute(cls, setpoint, input_, last_input, last_error):
        error = setpoint - input_
        d_input = input_ - (last_input if last_input is not None else input_)
        d_error = error - (last_error if last_error is not None else error)
        return cls(error, d_input, d_error)


class PID:
    """Minimal first-party reimplementation of simple_pid.PID's observable behaviour."""

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

        self._min_output, self._max_output = None, None
        self._auto_mode = True
        self.proportional_on_measurement = proportional_on_measurement
        self.error_map = error_map

        self._proportional = 0
        self._integral = 0
        self._derivative = 0

        self._last_time = None
        self._last_output = None
        self._last_error = None
        self._last_input = None

        self.output_limits = output_limits
        self.auto_mode = auto_mode

    def _resolve_dt(self, dt, now):
        if dt is not None:
            if dt <= 0:
                raise ValueError("dt has negative value {}, must be positive".format(dt))
            return dt
        if self._last_time is None:
            return 1e-16
        elapsed = now - self._last_time
        return elapsed if elapsed else 1e-16

    def _should_skip(self, dt):
        return (
            self.sample_time is not None
            and self._last_time is not None
            and dt < self.sample_time
            and self._last_output is not None
        )

    def _apply_error_map(self, error):
        if self.error_map is not None:
            return self.error_map(error)
        return error

    def _proportional_term(self, error, d_input):
        term = self.Kp * error
        if self.proportional_on_measurement:
            term -= self.Kp * d_input
        return term

    def _integral_term(self, error, dt):
        self._integral += self.Ki * error * dt
        return _clamp(self._integral, self.output_limits)

    def _derivative_term(self, d_input, dt):
        if dt > 0:
            return -self.Kd * d_input / dt
        return 0

    def __call__(self, input_, dt=None):
        if not self.auto_mode:
            return self._last_output

        now = _TimeSource.now()
        dt = self._resolve_dt(dt, now)

        if self._should_skip(dt):
            return self._last_output

        terms = _ErrorTerms.compute(self.setpoint, input_, self._last_input, self._last_error)
        error = self._apply_error_map(terms.error)

        self._proportional = self._proportional_term(error, terms.d_input)
        self._integral = self._integral_term(error, dt)
        self._derivative = self._derivative_term(terms.d_input, dt)

        output = self._proportional + self._integral + self._derivative
        output = _clamp(output, self.output_limits)

        self._last_output = output
        self._last_input = input_
        self._last_error = error
        self._last_time = now

        return output

    def __repr__(self):
        return (
            "{self.__class__.__name__}("
            "Kp={self.Kp!r}, Ki={self.Ki!r}, Kd={self.Kd!r}, "
            "setpoint={self.setpoint!r}, sample_time={self.sample_time!r}, "
            "output_limits={self.output_limits!r}, auto_mode={self.auto_mode!r}, "
            "proportional_on_measurement={self.proportional_on_measurement!r}, "
            "error_map={self.error_map!r}"
            ")"
        ).format(self=self)

    @property
    def components(self):
        return self._proportional, self._integral, self._derivative

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
            self._auto_mode = True
            self.reset()
            if last_output is not None:
                self._integral = last_output
                self._integral = _clamp(self._integral, self.output_limits)
        else:
            self._auto_mode = enabled

    @property
    def output_limits(self):
        return self._min_output, self._max_output

    @output_limits.setter
    def output_limits(self, limits):
        if limits is None:
            self._min_output, self._max_output = None, None
            return

        min_output, max_output = limits

        if None not in (min_output, max_output) and max_output < min_output:
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

        self._last_time = None
        self._last_output = None
        self._last_input = None
        self._last_error = None
