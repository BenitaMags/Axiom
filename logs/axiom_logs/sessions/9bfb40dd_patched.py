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


class Machine:
    """Minimal stand-in for transitions.Machine covering the usage in this file.

    Supports:
      - Machine(model=..., states=[...], initial=...)
      - add_transition(trigger, source, dest, conditions=None, after=None)
        where source may be a state name, a list of state names, or "*"
        (meaning any state).
      - Triggers become callable methods on the model, e.g. model.start_fill()
      - model.state attribute reflects current state.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._transitions = {}  # trigger -> list of _Transition

        model.state = initial

        model.machine = self

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if conditions is not None and not isinstance(conditions, (list, tuple)):
            conditions = [conditions]
        if source == "*":
            sources = ["*"]
        elif isinstance(source, (list, tuple)):
            sources = list(source)
        else:
            sources = [source]

        trans = _Transition(trigger, sources, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(trans)

        if not hasattr(self.model, trigger):
            setattr(self.model, trigger, self._make_trigger(trigger))

    def _make_trigger(self, trigger):
        def _trigger_fn(*args, **kwargs):
            return self._fire(trigger, *args, **kwargs)
        return _trigger_fn

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        matched_source = False
        for trans in self._transitions.get(trigger, []):
            if "*" in trans.source or current in trans.source:
                matched_source = True
                if trans.conditions:
                    ok = True
                    for cond in trans.conditions:
                        fn = getattr(model, cond) if isinstance(cond, str) else cond
                        if not fn(*args, **kwargs):
                            ok = False
                            break
                    if not ok:
                        continue
                model.state = trans.dest
                if trans.after:
                    afters = trans.after
                    if not isinstance(afters, (list, tuple)):
                        afters = [afters]
                    for after in afters:
                        fn = getattr(model, after) if isinstance(after, str) else after
                        fn(*args, **kwargs)
                return True
        if not matched_source:
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
