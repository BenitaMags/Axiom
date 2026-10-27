"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form (e.g., different decomposition, restructured anti-windup).
"""

from pi_support import clamp, PIParameters


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self._params = PIParameters(
            setpoint=setpoint_c,
            kp=kp,
            ki=ki,
            max_pressure=max_pressure_bar,
            i_limit=i_limit,
        )
        self.integral = 0.0
        self.tripped = False

    # -- attribute passthroughs to keep the flat public surface assignable --

    @property
    def setpoint(self):
        return self._params.setpoint

    @setpoint.setter
    def setpoint(self, value):
        self._params.setpoint = value

    @property
    def kp(self):
        return self._params.kp

    @kp.setter
    def kp(self, value):
        self._params.kp = value

    @property
    def ki(self):
        return self._params.ki

    @ki.setter
    def ki(self, value):
        self._params.ki = value

    @property
    def max_pressure(self):
        return self._params.max_pressure

    @max_pressure.setter
    def max_pressure(self, value):
        self._params.max_pressure = value

    @property
    def i_limit(self):
        return self._params.i_limit

    @i_limit.setter
    def i_limit(self, value):
        self._params.i_limit = value

    def reset(self):
        self.integral = 0.0
        self.tripped = False

    def _check_interlock(self, pressure: float) -> bool:
        if pressure >= self._params.max_pressure:
            self.tripped = True
        return self.tripped

    def _accumulate_integral(self, error: float, dt: float) -> float:
        self.integral += error * dt
        self.integral = clamp(self.integral, -self._params.i_limit, self._params.i_limit)
        return self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        # Safety interlock: an over-pressure event latches a trip and closes the valve.
        if self._check_interlock(pressure):
            return 0.0

        error = self._params.setpoint - temperature
        integral = self._accumulate_integral(error, dt)
        output = self._params.kp * error + self._params.ki * integral
        return clamp(output, 0.0, 100.0)
