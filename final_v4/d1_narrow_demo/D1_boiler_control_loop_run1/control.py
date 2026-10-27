"""control -- first-party PI boiler control with an over-pressure interlock.

This module wires together a small value object for the tuning constants, a
generic clamping helper (borrowed from ``pid_math``), and a short pipeline of
stages that together reproduce the original PI-with-anti-windup behaviour.
"""

from dataclasses import dataclass

from pid_math import clamp


@dataclass
class Gains:
    """PI gains, grouped so they travel together instead of as loose floats."""

    kp: float
    ki: float


@dataclass
class Limits:
    """Safety/anti-windup bounds, grouped as a single value object."""

    max_pressure: float
    i_limit: float


class BoilerControl:
    def __init__(self, setpoint_c=80.0, kp=2.0, ki=0.4,
                 max_pressure_bar=10.0, i_limit=50.0):
        self.setpoint = setpoint_c
        self.gains = Gains(kp, ki)
        self.limits = Limits(max_pressure_bar, i_limit)
        self.integral = 0.0
        self.tripped = False

    # -- public read-only views onto the grouped constants, kept so the
    # public attribute surface of the class is unchanged. --
    @property
    def kp(self):
        return self.gains.kp

    @property
    def ki(self):
        return self.gains.ki

    @property
    def max_pressure(self):
        return self.limits.max_pressure

    @property
    def i_limit(self):
        return self.limits.i_limit

    def reset(self):
        self.integral = 0.0
        self.tripped = False

    # -- pipeline stages -------------------------------------------------

    def _interlock_stage(self, pressure: float) -> bool:
        """Latch a trip on over-pressure; report whether the loop is tripped."""
        if pressure >= self.limits.max_pressure:
            self.tripped = True
        return self.tripped

    def _integral_stage(self, error: float, dt: float) -> float:
        """Accumulate the integral term and clamp it (anti-windup)."""
        self.integral += error * dt
        self.integral = clamp(self.integral, -self.limits.i_limit, self.limits.i_limit)
        return self.integral

    def _output_stage(self, error: float, integral: float) -> float:
        output = self.gains.kp * error + self.gains.ki * integral
        return clamp(output, 0.0, 100.0)

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        if self._interlock_stage(pressure):
            return 0.0

        error = self.setpoint - temperature
        integral = self._integral_stage(error, dt)
        return self._output_stage(error, integral)
