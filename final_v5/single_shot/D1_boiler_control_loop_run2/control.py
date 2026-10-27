# CHOSEN-TARGET: umodbus
"""control -- first-party PI boiler control with an over-pressure interlock.

Restructured: the anti-windup integrator is now its own small helper object
composed into the controller, and the interlock is a separate latch object,
instead of everything being inlined in a single `step` method.
"""


class ClampedIntegrator:
    """An accumulator that clamps itself within [-limit, +limit]."""

    def __init__(self, limit: float):
        self.limit = limit
        self.value = 0.0

    def reset(self):
        self.value = 0.0

    def accumulate(self, amount: float) -> float:
        self.value += amount
        if self.value > self.limit:
            self.value = self.limit
        elif self.value < -self.limit:
            self.value = -self.limit
        return self.value


class OverPressureLatch:
    """A latching trip: once tripped, stays tripped until reset."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.latched = False

    def reset(self):
        self.latched = False

    def evaluate(self, pressure: float) -> bool:
        if pressure >= self.threshold:
            self.latched = True
        return self.latched


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self._integrator = ClampedIntegrator(i_limit)
        self._interlock = OverPressureLatch(max_pressure_bar)

    @property
    def integral(self) -> float:
        return self._integrator.value

    @property
    def tripped(self) -> bool:
        return self._interlock.latched

    def reset(self):
        self._integrator.reset()
        self._interlock.reset()

    def _compute_error(self, temperature: float) -> float:
        return self.setpoint - temperature

    def _pi_output(self, error: float, dt: float) -> float:
        integral_term = self._integrator.accumulate(error * dt)
        return self.kp * error + self.ki * integral_term

    @staticmethod
    def _saturate(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
        if value < lo:
            return lo
        if value > hi:
            return hi
        return value

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._interlock.evaluate(pressure):
            return 0.0

        error = self._compute_error(temperature)
        raw_output = self._pi_output(error, dt)
        return self._saturate(raw_output)
