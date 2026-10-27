# CHOSEN-TARGET: umodbus
"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic, restructured into a state-machine style
implementation: the interlock and PI-with-anti-windup computations are
factored into small, separately testable helper methods rather than a single
linear function body.
"""


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self.integral = 0.0
        self.tripped = False

    def reset(self):
        self.integral = 0.0
        self.tripped = False

    def _check_interlock(self, pressure: float) -> bool:
        """Update and return the latched trip state for the given pressure."""
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped

    def _accumulate(self, error: float, dt: float) -> float:
        """Integrate error and apply anti-windup clamping; return the integral."""
        raw = self.integral + error * dt
        self.integral = max(-self.i_limit, min(self.i_limit, raw))
        return self.integral

    def _pi_output(self, error: float, integral: float) -> float:
        raw_output = self.kp * error + self.ki * integral
        return max(0.0, min(100.0, raw_output))

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._check_interlock(pressure):
            return 0.0

        error = self.setpoint - temperature
        integral = self._accumulate(error, dt)
        return self._pi_output(error, integral)
