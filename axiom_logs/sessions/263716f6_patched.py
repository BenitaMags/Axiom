"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when a triggered transition has no matching source/condition."""


class _Machine:
    """Minimal stand-in for transitions.Machine covering the API used here."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        model.state = initial

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        model = self.model

        if isinstance(source, str):
            sources = [source]
        else:
            sources = list(source)

        conditions_list = []
        if conditions is not None:
            if isinstance(conditions, str):
                conditions_list = [conditions]
            else:
                conditions_list = list(conditions)

        after_list = []
        if after is not None:
            if isinstance(after, str):
                after_list = [after]
            else:
                after_list = list(after)

        def trigger_fn(*args, **kwargs):
            current = model.state
            if sources != ["*"] and current not in sources:
                raise MachineError(
                    "Can't trigger event %s from state %s!" % (trigger, current)
                )

            for cond_name in conditions_list:
                cond_fn = getattr(model, cond_name)
                if not cond_fn():
                    return False

            model.state = dest

            for after_name in after_list:
                after_fn = getattr(model, after_name)
                after_fn()

            return True

        setattr(model, trigger, trigger_fn)


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0

        self.machine = _Machine(model=self, states=STATES, initial="IDLE")
        self.machine.add_transition("start_fill", "IDLE", "FILLING",
                                    conditions="can_fill", after="open_inlet")
        self.machine.add_transition("level_reached", "FILLING", "HOLDING",
                                    after="close_inlet")
        self.machine.add_transition("start_drain", ["HOLDING", "FILLING"], "DRAINING",
                                    after="open_outlet")
        self.machine.add_transition("empty", "DRAINING", "IDLE", after="close_outlet")
        self.machine.add_transition("trip", "*", "FAULT", after="safe_all")

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
