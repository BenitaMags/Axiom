"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form (e.g., different decomposition, restructured anti-windup).
"""

from pi_support import clamp, InterlockState, PIGains


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self._gains = PIGains(kp, ki)
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self.integral = 0.0
        self._interlock = InterlockState()

    @property
    def kp(self):
        return self._gains.kp

    @kp.setter
    def kp(self, value):
        self._gains = self._gains.with_kp(value)

    @property
    def ki(self):
        return self._gains.ki

    @ki.setter
    def ki(self, value):
        self._gains = self._gains.with_ki(value)

    @property
    def tripped(self):
        return self._interlock.tripped

    @tripped.setter
    def tripped(self, value):
        self._interlock.tripped = value

    def reset(self):
        self.integral = 0.0
        self._interlock.reset()

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        self._interlock.observe(pressure, self.max_pressure)
        if self._interlock.tripped:
            return 0.0

        error = self.setpoint - temperature
        self.integral = clamp(self.integral + error * dt, -self.i_limit, self.i_limit)
        output = self._gains.kp * error + self._gains.ki * self.integral
        return clamp(output, 0.0, 100.0)
