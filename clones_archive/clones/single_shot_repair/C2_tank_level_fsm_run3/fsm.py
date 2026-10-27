# CHOSEN-TARGET: first-party
"""fsm -- finite-state controller for a fill/drain tank process.


FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping is implemented as first-party code (no external FSM
library dependency).
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0
        self.state = "IDLE"

    # --- first-party guards and commands -------------------------------------
    def can_fill(self) -> bool:
        return interlock_ok(self._level)

    def open_inlet(self):
        self.inlet, self.outlet = True, False

    def close_inlet(self):
        self.inlet = False

    def open_outlet(self):
        self.inlet, self.outlet = False, True

    def close_outlet(self):
        self.outlet = False

    def safe_all(self):
        self.inlet = self.outlet = False

    # --- first-party state machine transitions --------------------------------
    def start_fill(self):
        if self.state == "IDLE" and self.can_fill():
            self.state = "FILLING"
            self.open_inlet()
            return True
        return False

    def level_reached(self):
        if self.state == "FILLING":
            self.state = "HOLDING"
            self.close_inlet()
            return True
        return False

    def start_drain(self):
        if self.state in ("HOLDING", "FILLING"):
            self.state = "DRAINING"
            self.open_outlet()
            return True
        return False

    def empty(self):
        if self.state == "DRAINING":
            self.state = "IDLE"
            self.close_outlet()
            return True
        return False

    def trip(self):
        self.state = "FAULT"
        self.safe_all()
        return True

    # --- first-party supervisory step ----------------------------------------
    def step(self, level: float):
        """Drive automatic transitions from the measured level."""
        self._level = level
        if not interlock_ok(level) and self.state != "FAULT":
            self.trip()
            return
        if self.state == "FILLING" and level >= self.high:
            self.level_reached()
        elif self.state == "DRAINING" and level <= self.low:
            self.empty()
