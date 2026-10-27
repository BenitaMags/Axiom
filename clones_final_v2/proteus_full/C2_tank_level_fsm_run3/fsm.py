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
        if self.source == "*":
            return True
        if isinstance(self.source, (list, tuple)):
            _spt_r1 = state in self.source
            return _spt_r1
        _spt_r2 = state == self.source
        return _spt_r2


class Machine:
    """Minimal stand-in for transitions.Machine covering the behaviour this
    program relies on: add_transition, conditions, after callbacks, and
    dynamically-created trigger methods bound onto the model.
    """

    def __init__(self, model=None, states=None, initial="IDLE"):
        self.model = model
        if not (states is not None):
            self.states = []
        else:
            self.states = list(states)
        self._transitions = {}

        if model is not None:
            model.state = initial

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        t = _Transition(trigger, source, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(t)

        def _trigger_fn(*args, __trigger=trigger, **kwargs):
            _spt_r3 = self._fire(__trigger, *args, **kwargs)
            return _spt_r3

        setattr(self.model, trigger, _trigger_fn)

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        matched_any_source = False
        for t in self._transitions.get(trigger, []):
            if not t.matches(current):
                continue
            matched_any_source = True
            if t.conditions is not None:
                cond_names = t.conditions
                if isinstance(cond_names, str):
                    cond_names = [cond_names]
                ok = True
                for cname in cond_names:
                    cond = getattr(model, cname)
                    if not cond():
                        ok = False
                        break
                if not ok:
                    continue
            model.state = t.dest
            if t.after is not None:
                after_names = t.after
                if isinstance(after_names, str):
                    after_names = [after_names]
                for aname in after_names:
                    getattr(model, aname)()
            return True
        if matched_any_source:
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
        _spt_r4 = interlock_ok(self._level)
        return _spt_r4

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
