"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic, restructured internally while remaining
functionally identical to the original PI + anti-windup + interlock design.
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
        """Latch the trip flag if pressure has reached/exceeded the threshold."""
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        if value < lower:
            return lower
        if value > upper:
            return upper
        return value

    def _accumulate(self, error: float, dt: float) -> float:
        candidate = self.integral + error * dt
        self.integral = self._clamp(candidate, -self.i_limit, self.i_limit)
        return self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        interlocked = self._check_interlock(pressure)
        if interlocked:
            return 0.0

        deviation = self.setpoint - temperature
        accumulated = self._accumulate(deviation, dt)

        proportional_term = self.kp * deviation
        integral_term = self.ki * accumulated
        raw_output = proportional_term + integral_term

        return self._clamp(raw_output, 0.0, 100.0)
