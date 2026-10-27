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

    def source_matches(self, state):
        if self.source == "*":
            return True
        if isinstance(self.source, (list, tuple)):
            return state in self.source
        return state == self.source


class _Machine:
    """Minimal stand-in for transitions.Machine covering the usage in this file."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.initial = initial
        self._transitions = {}

        model.state = initial

        def _make_trigger(name):
            def trigger(*args, **kwargs):
                return self._fire(name, *args, **kwargs)
            return trigger

        # placeholder; triggers get bound as add_transition is called
        self._trigger_names = set()

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        conds = conditions
        if conds is not None and not isinstance(conds, (list, tuple)):
            conds = [conds]
        afters = after
        if afters is not None and not isinstance(afters, (list, tuple)):
            afters = [afters]

        t = _Transition(trigger, source, dest, conditions=conds, after=afters)
        self._transitions.setdefault(trigger, []).append(t)

        if trigger not in self._trigger_names:
            self._trigger_names.add(trigger)

            def make_fn(name):
                def fn(*args, **kwargs):
                    return self._fire(name, *args, **kwargs)
                return fn

            setattr(self.model, trigger, make_fn(trigger))

    def _fire(self, trigger_name, *args, **kwargs):
        model = self.model
        current = model.state
        candidates = self._transitions.get(trigger_name, [])
        for t in candidates:
            if not t.source_matches(current):
                continue
            if t.conditions:
                ok = True
                for cond in t.conditions:
                    fn = getattr(model, cond) if isinstance(cond, str) else cond
                    if not fn():
                        ok = False
                        break
                if not ok:
                    continue
            model.state = t.dest
            if t.after:
                for cb in t.after:
                    fn = getattr(model, cb) if isinstance(cb, str) else cb
                    fn()
            return True
        return False


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
