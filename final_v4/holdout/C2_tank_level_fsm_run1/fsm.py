"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is delegated to a small generic transition table
executor (see `state_engine.py`). The transition guards, valve commands, and
interlocks remain first-party logic wired into that engine.
"""
from alarms import interlock_ok
from state_engine import StateEngine, StateEngineError, Transition

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    pass


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0
        self.state = "IDLE"

        self._engine = StateEngine(
            owner=self,
            initial="IDLE",
            transitions=(
                Transition("start_fill", "IDLE", "FILLING",
                           conditions="can_fill", after="open_inlet"),
                Transition("level_reached", "FILLING", "HOLDING",
                           after="close_inlet"),
                Transition("start_drain", ("HOLDING", "FILLING"), "DRAINING",
                           after="open_outlet"),
                Transition("empty", "DRAINING", "IDLE", after="close_outlet"),
                Transition("trip", "*", "FAULT", after="safe_all"),
            ),
            error_cls=MachineError,
        )

        for trigger_name in ("start_fill", "level_reached", "start_drain",
                              "empty", "trip"):
            setattr(self, trigger_name, self._make_trigger(trigger_name))

    def _make_trigger(self, name):
        def _call(*args, **kwargs):
            return self._engine.fire(name)
        return _call

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
