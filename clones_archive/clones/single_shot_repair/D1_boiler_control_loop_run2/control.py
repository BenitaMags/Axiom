"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form (e.g., different decomposition, restructured anti-windup).
"""


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


class _Interlock:
    """Latching over-pressure trip."""

    def __init__(self, max_pressure_bar: float):
        self.max_pressure = max_pressure_bar
        self.tripped = False

    def reset(self):
        self.tripped = False

    def evaluate(self, pressure: float) -> bool:
        if pressure >= self.max_pressure:
            self.tripped = True
        return self.tripped


class _PITerm:
    """Proportional-integral accumulator with anti-windup clamping."""

    def __init__(self, kp: float, ki: float, i_limit: float):
        self.kp = kp
        self.ki = ki
        self.i_limit = i_limit
        self.accum = 0.0

    def reset(self):
        self.accum = 0.0

    def update(self, error: float, dt: float) -> float:
        self.accum += error * dt
        self.accum = _clamp(self.accum, -self.i_limit, self.i_limit)
        return self.kp * error + self.ki * self.accum


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self._pi = _PITerm(kp, ki, i_limit)
        self._interlock = _Interlock(max_pressure_bar)

    @property
    def integral(self) -> float:
        return self._pi.accum

    @property
    def tripped(self) -> bool:
        return self._interlock.tripped

    def reset(self):
        self._pi.reset()
        self._interlock.reset()

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._interlock.evaluate(pressure):
            return 0.0

        error = self.setpoint - temperature
        output = self._pi.update(error, dt)
        return _clamp(output, 0.0, 100.0)
