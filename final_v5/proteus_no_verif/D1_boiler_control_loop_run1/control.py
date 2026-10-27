"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic, restructured internally as a small
pipeline (interlock check -> PI computation -> anti-windup clamp -> output
clamp) while keeping the exact same public surface as the original.
"""

from clamping import clamp


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

    def _check_interlock(self, pressure: float) -> None:
        # Safety interlock: an over-pressure event latches a trip and closes the valve.
        if pressure >= self.max_pressure:
            self.tripped = True

    def _accumulate_integral(self, error: float, dt: float) -> None:
        self.integral += error * dt
        # Anti-windup: clamp the accumulated integral term.
        self.integral = clamp(self.integral, -self.i_limit, self.i_limit)

    def _pi_output(self, error: float) -> float:
        return self.kp * error + self.ki * self.integral

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        self._check_interlock(pressure)
        if self.tripped:
            return 0.0

        error = self.setpoint - temperature
        self._accumulate_integral(error, dt)
        output = self._pi_output(error)
        return clamp(output, 0.0, 100.0)
