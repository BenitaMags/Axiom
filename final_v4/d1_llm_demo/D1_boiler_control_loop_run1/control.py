"""control -- first-party PI boiler control with an over-pressure interlock.

Restructured version: gains are held in a small value object, the integral
term is delegated to a reusable `Accumulator`, and `step` is decomposed into
a two-stage pipeline (interlock check, then PI computation) instead of one
long function.
"""

from dataclasses import dataclass

from pi_math import Accumulator, clamp


@dataclass
class _Gains:
    """Configuration values for the controller, grouped together."""

    setpoint: float
    kp: float
    ki: float
    max_pressure: float
    i_limit: float


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self._gains = _Gains(setpoint_c, kp, ki, max_pressure_bar, i_limit)
        self._acc = Accumulator()
        self.tripped = False

    # -- public attributes, preserved for compatibility ------------------

    @property
    def setpoint(self):
        return self._gains.setpoint

    @setpoint.setter
    def setpoint(self, value):
        self._gains.setpoint = value

    @property
    def kp(self):
        return self._gains.kp

    @kp.setter
    def kp(self, value):
        self._gains.kp = value

    @property
    def ki(self):
        return self._gains.ki

    @ki.setter
    def ki(self, value):
        self._gains.ki = value

    @property
    def max_pressure(self):
        return self._gains.max_pressure

    @max_pressure.setter
    def max_pressure(self, value):
        self._gains.max_pressure = value

    @property
    def i_limit(self):
        return self._gains.i_limit

    @i_limit.setter
    def i_limit(self, value):
        self._gains.i_limit = value

    @property
    def integral(self):
        return self._acc.value

    @integral.setter
    def integral(self, value):
        self._acc.value = value

    # -- behaviour ---------------------------------------------------------

    def reset(self):
        self._acc.reset()
        self.tripped = False

    def _interlock_tripped(self, pressure: float) -> bool:
        """Latch the trip on over-pressure and report the current state."""
        if pressure >= self._gains.max_pressure:
            self.tripped = True
        return self.tripped

    def _pi_output(self, temperature: float, dt: float) -> float:
        """Compute the PI valve command, with anti-windup on the integral."""
        error = self._gains.setpoint - temperature
        integral = self._acc.add(error * dt, self._gains.i_limit)
        output = self._gains.kp * error + self._gains.ki * integral
        return clamp(output, 0.0, 100.0)

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._interlock_tripped(pressure):
            return 0.0
        return self._pi_output(temperature, dt)
