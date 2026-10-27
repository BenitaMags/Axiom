# CHOSEN-TARGET: first-party
"""fsm -- finite-state controller for a fill/drain tank process.


FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping is implemented as first-party code (no external state
machine library dependency).
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

        # transition table: event -> list of (source_states_or_wildcard, dest, conditions, after)
        self._transitions = {
            "start_fill": [("IDLE", "FILLING", ["can_fill"], ["open_inlet"])],
            "level_reached": [("FILLING", "HOLDING", [], ["close_inlet"])],
            "start_drain": [(("HOLDING", "FILLING"), "DRAINING", [], ["open_outlet"])],
            "empty": [("DRAINING", "IDLE", [], ["close_outlet"])],
            "trip": [("*", "FAULT", [], ["safe_all"])],
        }

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

    # --- first-party state machine engine ------------------------------------
    def _fire(self, event_name: str) -> bool:
        rules = self._transitions.get(event_name, [])
        for sources, dest, conditions, after in rules:
            if sources == "*":
                matches = True
            elif isinstance(sources, tuple):
                matches = self.state in sources
            else:
                matches = self.state == sources

            if not matches:
                continue

            if all(getattr(self, cond)() for cond in conditions):
                self.state = dest
                for action in after:
                    getattr(self, action)()
                return True
        return False

    def start_fill(self):
        return self._fire("start_fill")

    def level_reached(self):
        return self._fire("level_reached")

    def start_drain(self):
        return self._fire("start_drain")

    def empty(self):
        return self._fire("empty")

    def trip(self):
        return self._fire("trip")

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
