"""controller -- PID pump controller regulating tank level to a setpoint.

The PID engine is the substitution target: `simple-pid` -> an in-house controller.
Because the substitution *removes* a third-party behavior, the PID computation becomes
first-party code that the LMA must re-implement and diversify -- hence Category C. The
surrounding logic (setpoint ramping, output clamping, AUTO/MANUAL mode) is already
first-party. The PID object is dependency-injected for testing.
"""
from time import monotonic

from io_map import speed_to_counts


def _bound(value, bounds):
    """Restrict value to the (low, high) tuple, tolerating None bounds/value."""
    if value is None:
        return None
    low, high = bounds
    if high is not None:
        if value > high:
            value = high
    if low is not None:
        if value < low:
            value = low
    return value


class PID:
    """Home-grown stand-in for simple_pid.PID, matching its observable behaviour."""

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
        self.error_map = error_map

        self._pom = proportional_on_measurement
        if not (time_fn is not None):
            self._clock = monotonic
        else:
            self._clock = time_fn

        self._p_term = 0.0
        self._i_term = 0.0
        self._d_term = 0.0

        self._prev_time = None
        self._prev_measurement = None
        self._prev_error = None

        self._out_low = None
        self._out_high = None

        self._last_output = None

        self._running = True

        self.output_limits = output_limits

        self._last_output = starting_output

        if not auto_mode:
            self.auto_mode = False

    def __call__(self, measurement, dt=None):
        if not self._running:
            _spt_r1 = self._last_output
            return _spt_r1

        now = self._clock()
        if not (dt is not None):
            if not (self._prev_time is not None):
                elapsed = None
            else:
                elapsed = now - self._prev_time
            if not elapsed:
                dt = 1e-16
            else:
                dt = elapsed
        else:
            if dt <= 0:
                raise ValueError("dt has negative value {}, must be positive".format(dt))

        skip_update = (
            self.sample_time is not None
            and self._prev_time is not None
            and dt < self.sample_time
        )
        if skip_update:
            _spt_r2 = self._last_output
            return _spt_r2

        error = self.setpoint - measurement
        if self.error_map is not None:
            error = self.error_map(error)
        if not (self._prev_measurement is not None):
            prior_measurement = measurement
        else:
            prior_measurement = self._prev_measurement
        measurement_delta = measurement - prior_measurement

        proportional = self.Kp * error
        if self._pom:
            proportional = proportional - self.Kp * measurement_delta
        self._p_term = proportional

        self._i_term = self._i_term + self.Ki * error * dt
        self._i_term = _bound(self._i_term, self.output_limits)
        if not (dt > 0):
            self._d_term = 0.0
        else:
            self._d_term = -self.Kd * (measurement_delta / dt)
        _spt_t1 = self._p_term + self._i_term

        total = _spt_t1 + self._d_term
        total = _bound(total, self.output_limits)

        self._prev_measurement = measurement
        self._prev_error = error
        self._prev_time = now
        self._last_output = total

        return total

    def __repr__(self):
        _spt_r3 = (
            '{cls}(Kp={kp!r}, Ki={ki!r}, Kd={kd!r}, setpoint={sp!r}, '
            'sample_time={st!r}, output_limits={ol!r}, auto_mode={am!r}, '
            'proportional_on_measurement={pom!r}, error_map={em!r})'
        ).format(
            cls=self.__class__.__name__,
            kp=self.Kp, ki=self.Ki, kd=self.Kd, sp=self.setpoint,
            st=self.sample_time, ol=self.output_limits, am=self.auto_mode,
            pom=self._pom, em=self.error_map,
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
        _spt_r6 = self._running
        return _spt_r6

    @auto_mode.setter
    def auto_mode(self, enabled):
        self.set_auto_mode(enabled)

    def set_auto_mode(self, enabled, last_output=None):
        turning_on = enabled and not self._running
        turning_off = (not enabled) and self._running

        if turning_on:
            self._running = True
            self.reset()
            if last_output is not None:
                self._i_term = _bound(last_output, self.output_limits)
        elif turning_off:
            self._running = False

    @property
    def output_limits(self):
        _spt_r7 = self._out_low, self._out_high
        return _spt_r7

    @output_limits.setter
    def output_limits(self, bounds):
        if bounds is None:
            self._out_low, self._out_high = None, None
            return

        low, high = bounds
        if low is not None:
            if high is not None:
                if high < low:
                    raise ValueError('lower limit must be less than upper limit')

        self._out_low, self._out_high = low, high
        self._i_term = _bound(self._i_term, self.output_limits)
        self._last_output = _bound(self._last_output, self.output_limits)

    @property
    def proportional_on_measurement(self):
        _spt_r8 = self._pom
        return _spt_r8

    @proportional_on_measurement.setter
    def proportional_on_measurement(self, enabled):
        self._pom = enabled

    def reset(self):
        self._p_term = 0.0
        self._i_term = _bound(0.0, self.output_limits)
        self._d_term = 0.0

        self._prev_time = self._clock()
        self._prev_measurement = None
        self._prev_error = None
        self._last_output = None


def _make_pid(kp, ki, kd, setpoint):
    _spt_r9 = PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))
    return _spt_r9


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
            _spt_r10 = speed_to_counts(0.0)
            return _spt_r10
        self._ramp_setpoint()
        speed = self.pid(level)
        speed = max(0.0, min(100.0, speed))  # clamp before actuation
        _spt_r11 = speed_to_counts(speed)
        return _spt_r11
