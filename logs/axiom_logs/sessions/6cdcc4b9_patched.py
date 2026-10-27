"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when no transition matches the current state (mirrors transitions.core.MachineError)."""
    pass


class _Machine:
    """Minimal stand-in for transitions.Machine, bound to a model instance."""

    def __init__(self, model, states, initial="IDLE"):
        self.model = model
        self.states = list(states)
        self.transitions = {}  # trigger -> list of (sources, dest, conditions, after)
        model.state = initial
        self._bind_state_checks()

    def _bind_state_checks(self):
        model = self.model
        for s in self.states:
            setattr(model, "is_" + s, (lambda st=s: model.state == st))

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if isinstance(source, str):
            sources = [source]
        else:
            sources = list(source)

        if conditions is None:
            cond_list = []
        elif isinstance(conditions, str):
            cond_list = [conditions]
        else:
            cond_list = list(conditions)

        if after is None:
            after_list = []
        elif isinstance(after, str):
            after_list = [after]
        else:
            after_list = list(after)

        self.transitions.setdefault(trigger, []).append(
            (sources, dest, cond_list, after_list)
        )

        if not hasattr(self.model, trigger):
            setattr(self.model, trigger, self._make_trigger(trigger))

    def _make_trigger(self, trigger):
        def _trigger(*args, **kwargs):
            model = self.model
            current = model.state
            matched_source = False
            for sources, dest, cond_list, after_list in self.transitions.get(trigger, []):
                if sources != ["*"] and current not in sources:
                    continue
                matched_source = True
                ok = True
                for cond_name in cond_list:
                    cond_fn = getattr(model, cond_name)
                    if not cond_fn():
                        ok = False
                        break
                if not ok:
                    continue
                model.state = dest
                for after_name in after_list:
                    getattr(model, after_name)()
                return True
            raise MachineError(
                "Can't trigger event %s from state %s!" % (trigger, current)
            )

        return _trigger


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
