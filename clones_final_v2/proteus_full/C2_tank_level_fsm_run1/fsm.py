"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when a trigger has no valid transition from the current state."""
    pass


class _Machine:
    """Minimal stand-in for transitions.Machine covering the API used here.

    Supports add_transition with conditions/after callbacks (by attribute name
    looked up on the model at call time), wildcard "*" source state, and lists
    of source states. Triggers are installed as bound-like methods on the
    model that drive state changes and set model.state.

    Matches transitions' semantics: if the trigger has no transitions defined
    from the current source state at all, raise MachineError. If a transition
    exists from the current state but its condition fails, silently return
    False (no exception) unless no other transition from that state matches.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.model.state = initial
        self._transitions = {}

        def is_state(state_name):
            def _check():
                _spt_r1 = self.model.state == state_name
                return _spt_r1
            return _check

        self.model.is_state = is_state  # not part of original API, harmless

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = list(self.states)
        elif not (isinstance(source, list)):
            sources = [source]
        else:
            sources = source

        if conditions is not None:
            if not isinstance(conditions, (list, tuple)):
                conditions = [conditions]
        if after is not None:
            if not isinstance(after, (list, tuple)):
                after = [after]

        for src in sources:
            self._transitions.setdefault(trigger, []).append(
                (src, dest, conditions, after)
            )

        if not hasattr(self.model, trigger):
            def _make_trigger(trigger_name):
                def _trigger(*args, **kwargs):
                    _spt_r2 = self._fire(trigger_name, *args, **kwargs)
                    return _spt_r2
                return _trigger
            setattr(self.model, trigger, _make_trigger(trigger))

    def _fire(self, trigger, *args, **kwargs):
        current = self.model.state
        candidates = self._transitions.get(trigger, [])
        matched_source = False
        for src, dest, conditions, after in candidates:
            if src != current:
                continue
            matched_source = True
            if conditions:
                ok = True
                for cond in conditions:
                    if not (isinstance(cond, str)):
                        fn = cond
                    else:
                        fn = getattr(self.model, cond)
                    if not fn():
                        ok = False
                        break
                if not ok:
                    continue
            self.model.state = dest
            if after:
                for cb in after:
                    if not (isinstance(cb, str)):
                        fn = cb
                    else:
                        fn = getattr(self.model, cb)
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
        Machine = _Machine  # first-party state-machine engine (was: transitions.Machine)

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
        _spt_r3 = interlock_ok(self._level)
        return _spt_r3

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
