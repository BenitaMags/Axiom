"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic, restructured as a small pipeline of
stages (interlock check -> integrator update -> output computation) driven
by a helper value object that owns the clamping arithmetic.
"""

from clamped_range import ClampedRange


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

    def _interlock(self, pressure: float) -> bool:
        """Update and return the latched trip state for this pressure reading."""
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped

    def _update_integral(self, error: float, dt: float) -> float:
        self.integral += error * dt
        windup = ClampedRange(-self.i_limit, self.i_limit)
        self.integral = windup.clamp(self.integral)
        return self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        # Safety interlock: an over-pressure event latches a trip and closes the valve.
        if self._interlock(pressure):
            return 0.0

        error = self.setpoint - temperature
        integral = self._update_integral(error, dt)
        output = self.kp * error + self.ki * integral

        valve_range = ClampedRange(0.0, 100.0)
        return valve_range.clamp(output)
