"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    pass


class _Transition:
    def __init__(self, trigger, source, dest, conditions=None, after=None):
        self.trigger = trigger
        if source == "*":
            self.sources = None
        elif isinstance(source, (list, tuple)):
            self.sources = list(source)
        else:
            self.sources = [source]
        self.dest = dest
        if conditions is None:
            self.conditions = []
        elif isinstance(conditions, (list, tuple)):
            self.conditions = list(conditions)
        else:
            self.conditions = [conditions]
        if after is None:
            self.after = []
        elif isinstance(after, (list, tuple)):
            self.after = list(after)
        else:
            self.after = [after]

    def matches(self, state):
        return self.sources is None or state in self.sources


class Machine:
    """Minimal stand-in for transitions.Machine covering the API used here."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.initial = initial
        self._transitions = {}
        model.state = initial

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        t = _Transition(trigger, source, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(t)

        if not hasattr(self.model, trigger):
            def _make_trigger(name):
                def _trigger(*args, **kwargs):
                    return self._fire(name, *args, **kwargs)
                return _trigger

            setattr(self.model, trigger, _make_trigger(trigger))

    def _fire(self, trigger, *args, **kwargs):
        current = self.model.state
        candidates = self._transitions.get(trigger, [])
        matched_any_source = False
        for t in candidates:
            if not t.matches(current):
                continue
            matched_any_source = True
            ok = True
            for cond in t.conditions:
                func = getattr(self.model, cond) if isinstance(cond, str) else cond
                if not func():
                    ok = False
                    break
            if not ok:
                continue
            self.model.state = t.dest
            for after in t.after:
                func = getattr(self.model, after) if isinstance(after, str) else after
                func()
            return True

        if not matched_any_source:
            raise MachineError(
                "Can't trigger event %s from state %s!" % (trigger, current)
            )
        return False


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0

        self.machine = Machine(model=self, states=STATES, initial="IDLE")
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
