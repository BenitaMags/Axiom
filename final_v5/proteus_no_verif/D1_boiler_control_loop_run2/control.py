"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form (e.g., different decomposition, restructured anti-windup).
"""

from pi_support import clamp, InterlockState


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self.integral = 0.0
        self._interlock = InterlockState(tripped=False)

    @property
    def tripped(self):
        return self._interlock.tripped

    @tripped.setter
    def tripped(self, value):
        self._interlock.tripped = value

    def reset(self):
        self.integral = 0.0
        self._interlock.reset()

    def _check_interlock(self, pressure: float) -> bool:
        if pressure >= self.max_pressure:
            self._interlock.trip()
        return self._interlock.tripped

    def _integrate(self, error: float, dt: float) -> float:
        self.integral = clamp(self.integral + error * dt, -self.i_limit, self.i_limit)
        return self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._check_interlock(pressure):
            return 0.0

        error = self.setpoint - temperature
        integral = self._integrate(error, dt)
        output = self.kp * error + self.ki * integral
        return clamp(output, 0.0, 100.0)
