"""control -- first-party PI boiler control with an over-pressure interlock.

This is the core application logic the LMA must rewrite into an equivalent but
structurally different form (e.g., different decomposition, restructured anti-windup).
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

    def step(self, temperature: float, pressure: float, dt: float = 1.0) -> float:
        """Return the valve command (0-100%) for the measured temperature/pressure."""
        # Safety interlock: an over-pressure event latches a trip and closes the valve.
        if pressure >= self.max_pressure:
            self.tripped = True
        if self.tripped:
            return 0.0

        error = self.setpoint - temperature
        self.integral = self.integral + error * dt
        _t0 = -self.i_limit
        _t1 = min(self.i_limit, self.integral)
        # Anti-windup: clamp the accumulated integral term.
        self.integral = max(_t0, _t1)
        _spt_t1 = self.kp * error
        output = _spt_t1 + self.ki * self.integral
        _t2 = min(100.0, output)
        _spt_r1 = max(0.0, _t2)
        return _spt_r1
