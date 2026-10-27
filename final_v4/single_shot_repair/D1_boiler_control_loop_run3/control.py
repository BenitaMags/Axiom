"""control -- first-party PI boiler control with an over-pressure interlock.

Restructured: the anti-windup integrator is factored out into its own small
helper class, and the interlock/PI computation are split into separate
methods rather than being interleaved in a single function body.
"""


class ClampedIntegrator:
    """An accumulator that clamps its running total to +/- limit."""

    def __init__(self, limit: float):
        self.limit = limit
        self.total = 0.0

    def reset(self):
        self.total = 0.0

    def accumulate(self, value: float, dt: float) -> float:
        self.total += value * dt
        self.total = max(-self.limit, min(self.limit, self.total))
        return self.total


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self._integrator = ClampedIntegrator(i_limit)
        self.tripped = False

    @property
    def integral(self) -> float:
        return self._integrator.total

    def reset(self):
        self._integrator.reset()
        self.tripped = False

    def _check_interlock(self, pressure: float) -> bool:
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped

    def _pi_output(self, temperature: float, dt: float) -> float:
        error = self.setpoint - temperature
        integral = self._integrator.accumulate(error, dt)
        return self.kp * error + self.ki * integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._check_interlock(pressure):
            return 0.0

        raw_output = self._pi_output(temperature, dt)
        return max(0.0, min(100.0, raw_output))
