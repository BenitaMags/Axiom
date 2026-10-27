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
    """Minimal re-implementation of the subset of `transitions.Machine` used here."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.transitions = {}  # trigger -> list of _Transition
        model.state = initial
        self._bind_model(model)

    def _bind_model(self, model):
        # Give the model a way to query its own state via attribute access.
        pass

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = list(self.states)
        elif not (isinstance(source, (list, tuple))):
            sources = [source]
        else:
            sources = list(source)

        if conditions is not None:
            if not isinstance(conditions, (list, tuple)):
                conditions = [conditions]
        if after is not None:
            if not isinstance(after, (list, tuple)):
                after = [after]

        trans_list = self.transitions.setdefault(trigger, [])
        for src in sources:
            trans_list.append(_Transition(trigger, src, dest, conditions, after))

        self._install_trigger(trigger)

    def _install_trigger(self, trigger):
        model = self.model

        def _trigger_fn(*args, **kwargs):
            _spt_r1 = self._fire(trigger, *args, **kwargs)
            return _spt_r1

        setattr(model, trigger, _trigger_fn)

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        candidates = self.transitions.get(trigger, [])
        matched_source = False
        for t in candidates:
            if t.source != current:
                continue
            matched_source = True
            ok = True
            if t.conditions:
                for cond in t.conditions:
                    if not (isinstance(cond, str)):
                        fn = cond
                    else:
                        fn = getattr(model, cond)
                    if not fn():
                        ok = False
                        break
            if not ok:
                continue
            model.state = t.dest
            if t.after:
                for cb in t.after:
                    if not (isinstance(cb, str)):
                        fn = cb
                    else:
                        fn = getattr(model, cb)
                    fn()
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
