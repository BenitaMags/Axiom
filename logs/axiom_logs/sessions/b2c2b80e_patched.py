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
    """First-party re-implementation of the subset of `simple-pid`'s PID class
    used by this program: same construction signature, same attributes, and
    the same call semantics (`pid(input_)` returns the clamped output).
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
    ):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.sample_time = sample_time

        self._min_output = None
        self._max_output = None
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

        if time_fn is not None:
            self.time_fn = time_fn
        else:
            self.time_fn = time.monotonic

        self.output_limits = output_limits
        self.auto_mode = auto_mode

    def __call__(self, input_, dt=None):
        if not self.auto_mode:
            return self._last_output

        now = self.time_fn()
        if dt is None and self._last_time is not None:
            dt = now - self._last_time
        elif dt is None:
            dt = 1e-16

        if dt <= 0:
            raise ValueError("dt has negative value {}, must be positive".format(dt))

        if self.sample_time is not None and self._last_time is not None and dt < self.sample_time:
            return self._last_output

        error = self.setpoint - input_
        d_input = input_ - (self._last_input if self._last_input is not None else input_)
        d_error = error - (self._last_error if self._last_error is not None else error)

        if self.error_map is not None:
            error = self.error_map(error)

        self._proportional = self.Kp * error
        if self.proportional_on_measurement:
            self._proportional -= self.Kp * d_input

        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral, self.output_limits)

        self._derivative = -self.Kd * d_input / dt

        output = self._proportional + self._integral + self._derivative
        output = self._clamp(output, self.output_limits)

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
            self.reset()
            self._integral = last_output if last_output is not None else 0
            self._integral = self._clamp(self._integral, self.output_limits)
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

        self._last_time = self.time_fn()
        self._last_output = None
        self._last_input = None
        self._last_error = None


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
