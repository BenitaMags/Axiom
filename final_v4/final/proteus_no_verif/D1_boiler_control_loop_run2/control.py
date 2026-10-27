"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic, structured as a small pipeline of stages
(interlock check -> PI computation -> anti-windup clamp -> output clamp)
built on a reusable clamped-range helper.
"""

from pi_support import ClampedRange


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self._integral_range = ClampedRange(-i_limit, i_limit)
        self._output_range = ClampedRange(0.0, 100.0)
        self.integral = 0.0
        self.tripped = False

    @property
    def i_limit(self):
        return self._integral_range.high

    @i_limit.setter
    def i_limit(self, value):
        self._integral_range = ClampedRange(-value, value)

    def reset(self):
        self.integral = 0.0
        self.tripped = False

    def _check_interlock(self, pressure: float) -> bool:
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped

    def _accumulate_integral(self, error: float, dt: float) -> float:
        self.integral += error * dt
        self.integral = self._integral_range.clamp(self.integral)
        return self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._check_interlock(pressure):
            return 0.0

        error = self.setpoint - temperature
        integral = self._accumulate_integral(error, dt)
        output = self.kp * error + self.ki * integral
        return self._output_range.clamp(output)
