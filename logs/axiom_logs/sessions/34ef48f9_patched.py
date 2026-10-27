"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class _Transition:
    __slots__ = ("trigger", "source", "dest", "conditions", "after")

    def __init__(self, trigger, source, dest, conditions=None, after=None):
        self.trigger = trigger
        self.source = source
        self.dest = dest
        self.conditions = conditions
        self.after = after

    def sources(self, states):
        if self.source == "*":
            return list(states)
        if isinstance(self.source, (list, tuple)):
            return list(self.source)
        return [self.source]


class Machine:
    """Minimal stand-in for transitions.Machine covering the behaviour this
    program relies on: add_transition with conditions/after callbacks,
    wildcard '*' sources, list-of-source transitions, and trigger methods
    bound onto the model that check conditions, change model.state, and
    call the after-callback."""

    def __init__(self, model=None, states=None, initial=None):
        self.model = model
        self.states = list(states) if states is not None else []
        self._transitions = {}  # trigger -> list of _Transition

        if model is not None:
            model.state = initial

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        t = _Transition(trigger, source, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(t)

        if self.model is not None and not hasattr(self.model, trigger):
            def _bound_trigger(*args, _trigger=trigger, **kwargs):
                return self._fire(_trigger, *args, **kwargs)
            setattr(self.model, trigger, _bound_trigger)

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        for t in self._transitions.get(trigger, []):
            srcs = t.sources(self.states)
            if current not in srcs:
                continue
            if t.conditions is not None:
                cond_names = t.conditions
                if isinstance(cond_names, (list, tuple)):
                    ok = all(getattr(model, c)() for c in cond_names)
                else:
                    ok = getattr(model, cond_names)()
                if not ok:
                    continue
            model.state = t.dest
            if t.after is not None:
                after_names = t.after
                if isinstance(after_names, (list, tuple)):
                    for a in after_names:
                        getattr(model, a)()
                else:
                    getattr(model, after_names)()
            return True
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
