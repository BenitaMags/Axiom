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
    __slots__ = ("trigger", "source", "dest", "conditions", "after")

    def __init__(self, trigger, source, dest, conditions=None, after=None):
        self.trigger = trigger
        self.source = source
        self.dest = dest
        self.conditions = conditions
        self.after = after

    def matches(self, state):
        return self.source == "*" or state in self.source


class Machine:
    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.initial = initial
        self._transitions = {}

        model.state = initial

        def is_state(s, _model=model):
            return _model.state == s

        setattr(model, "is_" + str(initial), lambda s=initial: is_state(s))

        for state in self.states:
            def _checker(_state=state):
                return self.model.state == _state
            setattr(model, "is_" + state, _checker)

        setattr(model, "trigger", self._trigger)

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if isinstance(source, str) and source != "*":
            source_list = [source]
        elif source == "*":
            source_list = "*"
        else:
            source_list = list(source)

        cond_list = []
        if conditions is not None:
            if isinstance(conditions, str):
                cond_list = [conditions]
            else:
                cond_list = list(conditions)

        after_list = []
        if after is not None:
            if isinstance(after, str):
                after_list = [after]
            else:
                after_list = list(after)

        t = _Transition(trigger, source_list, dest, cond_list, after_list)
        self._transitions.setdefault(trigger, []).append(t)

        def _bound_trigger(*args, _trigger=trigger, **kwargs):
            return self._fire(_trigger, *args, **kwargs)

        setattr(self.model, trigger, _bound_trigger)

    def _fire(self, trigger, *args, **kwargs):
        candidates = self._transitions.get(trigger, [])
        current_state = self.model.state
        matched_any = False
        for t in candidates:
            if t.matches(current_state):
                matched_any = True
                ok = True
                for cond_name in t.conditions:
                    cond_fn = getattr(self.model, cond_name)
                    if not cond_fn():
                        ok = False
                        break
                if not ok:
                    continue
                self.model.state = t.dest
                for after_name in t.after:
                    after_fn = getattr(self.model, after_name)
                    after_fn()
                return True
        if not matched_any:
            raise MachineError(
                "Can't trigger event %s from state %s!" % (trigger, current_state)
            )
        return False

    def _trigger(self, name, *args, **kwargs):
        return self._fire(name, *args, **kwargs)


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
