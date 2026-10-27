"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when a trigger cannot be fired from the current state."""
    pass


class _Transition:
    __slots__ = ("trigger", "source", "dest", "conditions", "after")

    def __init__(self, trigger, source, dest, conditions=None, after=None):
        self.trigger = trigger
        self.source = source
        self.dest = dest
        self.conditions = conditions
        self.after = after


class Machine:
    """Minimal stand-in for transitions.Machine, covering the API used here."""

    def __init__(self, model=None, states=None, initial=None):
        self.model = model
        if not (states is not None):
            self.states = []
        else:
            self.states = list(states)
        self.transitions = {}  # trigger -> list of _Transition

        if model is not None:
            if initial is not None:
                model.state = initial

        if model is not None:
            model.machine = self

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if conditions is not None:
            if not isinstance(conditions, (list, tuple)):
                conditions = [conditions]
        if after is not None:
            if not isinstance(after, (list, tuple)):
                after = [after]

        if source == "*":
            sources = list(self.states)
        elif not (isinstance(source, (list, tuple))):
            sources = [source]
        else:
            sources = list(source)

        t = _Transition(trigger, sources, dest, conditions=conditions, after=after)
        self.transitions.setdefault(trigger, []).append(t)

        self._attach_trigger(trigger)

    def _attach_trigger(self, trigger):
        model = self.model

        def _fn(*args, **kwargs):
            _spt_r1 = self._fire(trigger, model, *args, **kwargs)
            return _spt_r1

        setattr(model, trigger, _fn)

    def _fire(self, trigger, model, *args, **kwargs):
        current = model.state
        candidates = self.transitions.get(trigger, [])
        matched_source = False
        for t in candidates:
            if current not in t.source:
                continue
            matched_source = True
            if t.conditions:
                ok = True
                for cond in t.conditions:
                    if not (callable(cond)):
                        fn = getattr(model, cond)
                    else:
                        fn = cond
                    if not fn():
                        ok = False
                        break
                if not ok:
                    continue
            model.state = t.dest
            if t.after:
                for cb in t.after:
                    if not (callable(cb)):
                        fn = getattr(model, cb)
                    else:
                        fn = cb
                    fn()
            return True
        if matched_source:
            return False
        raise MachineError(
            "Can't trigger event %s from state %s!" % (trigger, current)
        )


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
        _spt_r2 = interlock_ok(self._level)
        return _spt_r2

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
        if not interlock_ok(level):
            if self.state != "FAULT":
                self.trip()
                return
        if self.state == "FILLING" and level >= self.high:
            self.level_reached()
        elif self.state == "DRAINING":
            if level <= self.low:
                self.empty()
