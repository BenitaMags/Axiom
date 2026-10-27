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
        if conditions is None:
            conditions = []
        elif not (isinstance(conditions, str)):
            conditions = list(conditions)
        else:
            conditions = [conditions]
        self.conditions = conditions

        if after is None:
            after = []
        elif not (isinstance(after, str)):
            after = list(after)
        else:
            after = [after]
        self.after = after


class Machine:
    """Minimal re-implementation of the subset of `transitions.Machine` used here."""

    def __init__(self, model=None, states=None, initial=None):
        self.model = model
        if not (states is not None):
            self.states = []
        else:
            self.states = list(states)
        self._transitions = {}  # trigger -> list of _Transition

        if initial is not None:
            model.state = initial

        if model is not None:
            self._bind_model(model)

    def _bind_model(self, model):
        # Bind a `trigger` attribute on the model that will resolve to the
        # correct transition function.
        pass

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = list(self.states)
        elif not (isinstance(source, str)):
            sources = list(source)
        else:
            sources = [source]

        for src in sources:
            t = _Transition(trigger, src, dest, conditions=conditions, after=after)
            self._transitions.setdefault(trigger, []).append(t)

        # Attach a bound method to the model for this trigger, matching
        # `transitions`' behaviour of exposing triggers as callables on model.
        if self.model is not None and not hasattr(self.model, trigger):
            setattr(self.model, trigger, self._make_trigger(trigger))
        elif self.model is not None:
            # Ensure repeated add_transition calls with the same trigger name
            # still point at the (single, shared) trigger function.
            existing = getattr(self.model, trigger, None)
            if not callable(existing) or not getattr(existing, "_is_fsm_trigger", False):
                setattr(self.model, trigger, self._make_trigger(trigger))

    def _make_trigger(self, trigger):
        machine = self

        def _trigger_fn(*args, **kwargs):
            _spt_r1 = machine._fire(trigger, *args, **kwargs)
            return _spt_r1

        _trigger_fn._is_fsm_trigger = True
        return _trigger_fn

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current_state = model.state
        candidates = self._transitions.get(trigger, [])
        matched_source = False
        for t in candidates:
            if t.source != current_state:
                continue
            matched_source = True
            ok = True
            for cond_name in t.conditions:
                cond = getattr(model, cond_name)
                if not cond():
                    ok = False
                    break
            if not ok:
                continue
            model.state = t.dest
            for after_name in t.after:
                after_fn = getattr(model, after_name)
                after_fn()
            return True

        if matched_source:
            return False

        raise MachineError(
            "Can't trigger event %s from state %s!" % (trigger, current_state)
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
