"""plant -- a minimal tank simulator used to exercise the controller in closed loop.

This is test/reference scaffolding (a first-order integrator), not part of the shipped
controller, but it provides the reference trajectory the equivalence oracle checks.
"""


class Tank:
    def __init__(self, level=0.0, inflow_gain=0.05, outflow=0.02, area=1.0):
        self.level = level
        self.inflow_gain = inflow_gain
        self.outflow = outflow
        self.area = area

    def step(self, pump_speed_pct, dt=1.0) -> float:
        inflow = self.inflow_gain * pump_speed_pct
        self.level = self.level + (inflow - self.outflow) * dt / self.area
        self.level = max(0.0, self.level)
        _spt_r1 = self.level
        return _spt_r1
