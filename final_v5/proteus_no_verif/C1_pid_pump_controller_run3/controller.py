"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
import time

from io_map import speed_to_counts


def _bounded(x, bounds):
    """Fit x within (lo, hi), where either side may be None to mean unbounded."""
    if x is None:
        return None
    lo, hi = bounds
    if hi is not None and x > hi:
        x = hi
    if lo is not None and x < lo:
        x = lo
    return x


class PID:
    """Stand-alone PID controller matching the observable behaviour that this
    project previously obtained from the third-party `simple-pid` package.

    Call the instance with a fresh measurement to get the next control output.
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
        time_fn=None,
        starting_output=0.0,
    ):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.sample_time = sample_time
        self.proportional_on_measurement = proportional_on_measurement
        self.error_map = error_map
        if not (time_fn is not None):
            self._clock = time.monotonic
        else:
            self._clock = time_fn

        # Term breakdown, exposed via .components
        self._p_term = 0.0
        self._i_term = 0.0
        self._d_term = 0.0

        # History needed to compute rate-of-change and elapsed time.
        self._prev_measurement = None
        self._prev_error = None
        self._prev_time = None

        # output_limits setter clamps _i_term / _last_output, so both
        # must exist first, and _auto_mode must exist before the
        # auto_mode setter runs.
        self._out_lo = None
        self._out_hi = None
        self._auto_mode = True
        self._last_output = starting_output

        self.output_limits = output_limits
        self.auto_mode = auto_mode

    def __call__(self, measurement, dt=None):
        if not self._auto_mode:
            _spt_r1 = self._last_output
            return _spt_r1

        moment = self._clock()
        if not (dt is not None):
            if not (self._prev_time is not None):
                elapsed = None
            else:
                elapsed = moment - self._prev_time
            if not elapsed:
                dt = 1e-16
            else:
                dt = elapsed
        else:
            if dt <= 0:
                raise ValueError('dt has negative value {}, must be positive'.format(dt))

        # Rate limiting: if not enough time has elapsed since the last
        # real computation, hand back the previously computed output.
        if (
            self.sample_time is not None
            and self._prev_time is not None
            and dt < self.sample_time
        ):
            _spt_r2 = self._last_output
            return _spt_r2

        error = self.setpoint - measurement
        if not (self._prev_measurement is not None):
            prior_measurement = measurement
        else:
            prior_measurement = self._prev_measurement
        if not (self._prev_error is not None):
            prior_error = error
        else:
            prior_error = self._prev_error
        measurement_change = measurement - prior_measurement
        # error_change would be (error - prior_error) but simple-pid derives
        # the derivative term from the measurement, not the error; kept here
        # only to document that distinction, not used further.
        del prior_error

        if self.error_map is not None:
            error = self.error_map(error)

        self._p_term = self.Kp * error
        if self.proportional_on_measurement:
            self._p_term = self._p_term - self.Kp * measurement_change

        self._i_term = self._i_term + self.Ki * error * dt
        self._i_term = _bounded(self._i_term, self.output_limits)
        _spt_t1 = -self.Kd * measurement_change

        self._d_term = _spt_t1 / dt
        _spt_t2 = self._p_term + self._i_term

        result = _spt_t2 + self._d_term
        result = _bounded(result, self.output_limits)

        self._prev_measurement = measurement
        self._prev_error = error
        self._prev_time = moment
        self._last_output = result

        return result

    def __repr__(self):
        _spt_r3 = (
            '{cls}(Kp={kp!r}, Ki={ki!r}, Kd={kd!r}, setpoint={sp!r}, '
            'sample_time={st!r}, output_limits={ol!r}, auto_mode={am!r}, '
            'proportional_on_measurement={pom!r}, error_map={em!r})'
        ).format(
            cls=self.__class__.__name__,
            kp=self.Kp,
            ki=self.Ki,
            kd=self.Kd,
            sp=self.setpoint,
            st=self.sample_time,
            ol=self.output_limits,
            am=self.auto_mode,
            pom=self.proportional_on_measurement,
            em=self.error_map,
        )
        return _spt_r3

    @property
    def components(self):
        _spt_r4 = self._p_term, self._i_term, self._d_term
        return _spt_r4

    @property
    def tunings(self):
        _spt_r5 = self.Kp, self.Ki, self.Kd
        return _spt_r5

    @tunings.setter
    def tunings(self, values):
        self.Kp, self.Ki, self.Kd = values

    @property
    def auto_mode(self):
        _spt_r6 = self._auto_mode
        return _spt_r6

    @auto_mode.setter
    def auto_mode(self, enabled):
        self.set_auto_mode(enabled)

    def set_auto_mode(self, enabled, last_output=None):
        turning_on = enabled and not self._auto_mode
        turning_off = (not enabled) and self._auto_mode

        if turning_on:
            self._auto_mode = True
            self.reset()
            if last_output is not None:
                self._i_term = _bounded(last_output, self.output_limits)
        elif turning_off:
            self._auto_mode = False

    @property
    def output_limits(self):
        _spt_r7 = self._out_lo, self._out_hi
        return _spt_r7

    @output_limits.setter
    def output_limits(self, limits):
        if limits is None:
            self._out_lo, self._out_hi = None, None
            return

        lo, hi = limits
        if lo is not None and hi is not None and hi < lo:
            raise ValueError('lower limit must be less than upper limit')

        self._out_lo, self._out_hi = lo, hi
        self._i_term = _bounded(self._i_term, self.output_limits)
        self._last_output = _bounded(self._last_output, self.output_limits)

    def reset(self):
        self._p_term = 0.0
        self._i_term = _bounded(0.0, self.output_limits)
        self._d_term = 0.0

        self._prev_time = self._clock()
        self._prev_measurement = None
        self._prev_error = None
        self._last_output = None


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
