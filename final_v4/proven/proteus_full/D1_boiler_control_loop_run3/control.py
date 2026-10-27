"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form (e.g., different decomposition, restructured anti-windup).
"""

from pi_math import clamp


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
        """Latch a trip if pressure is at or beyond the limit; return trip state."""
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped

    def _accumulate_integral(self, error: float, dt: float) -> float:
        """Update and anti-windup clamp the integral term, returning its value."""
        self.integral += error * dt
        self.integral = clamp(self.integral, -self.i_limit, self.i_limit)
        return self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        # Safety interlock: an over-pressure event latches a trip and closes the valve.
        if self._check_interlock(pressure):
            return 0.0

        error = self.setpoint - temperature
        integral_term = self._accumulate_integral(error, dt)
        output = self.kp * error + self.ki * integral_term
        return clamp(output, 0.0, 100.0)
