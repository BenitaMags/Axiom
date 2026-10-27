"""control -- boiler PI regulation with an over-pressure lockout.

Behavioural clone of the reference controller: identical numbers out for identical
numbers in.  The organisation is different.  Instead of one ``step`` method that
re-tests a boolean flag and clamps inline, the controller is a two-mode state machine
(REGULATING / LOCKED_OUT) whose per-mode behaviour lives in a dispatch table, and the
anti-windup integrator is a component of its own.
"""
from saturation import Rails, VALVE_TRAVEL

REGULATING = "regulating"
LOCKED_OUT = "locked-out"


class Integrator:
    """Discrete-time integrator whose stored state is pinned to +/- ``limit`` after
    every update (clamp-after-accumulate anti-windup)."""

    def __init__(self):
        self.value = 0.0

    def clear(self):
        self.value = 0.0

    def accumulate(self, error: float, dt: float, limit: float) -> float:
        self.value = Rails(-limit, limit).pin(self.value + error * dt)
        return self.value


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.kp = kp
        self.ki = ki
        self.max_pressure = max_pressure_bar
        self.i_limit = i_limit
        self._integrator = Integrator()
        self._mode = REGULATING

    # ---- state exposed under the reference implementation's attribute names ----
    @property
    def integral(self) -> float:
        return self._integrator.value

    @integral.setter
    def integral(self, value: float):
        self._integrator.value = value

    @property
    def tripped(self) -> bool:
        return self._mode == LOCKED_OUT

    @tripped.setter
    def tripped(self, flag: bool):
        self._mode = LOCKED_OUT if flag else REGULATING

    def reset(self):
        self._integrator.clear()
        self._mode = REGULATING

    # ---- state machine ----
    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        self._mode = self._transition(pressure)
        return self._BEHAVIOUR[self._mode](self, temperature, dt)

    def _transition(self, pressure: float) -> str:
        # The only transition is REGULATING -> LOCKED_OUT, taken on over-pressure and
        # never taken back: the lockout is a latch that only reset() releases.
        if pressure >= self.max_pressure:
            return LOCKED_OUT
        return self._mode

    def _locked_out(self, temperature: float, dt: float) -> float:
        return 0.0  # valve shut; the integrator is frozen while locked out

    def _regulating(self, temperature: float, dt: float) -> float:
        error = self.setpoint - temperature
        integral = self._integrator.accumulate(error, dt, self.i_limit)
        return VALVE_TRAVEL.pin(self.kp * error + self.ki * integral)

    _BEHAVIOUR = {REGULATING: _regulating, LOCKED_OUT: _locked_out}
