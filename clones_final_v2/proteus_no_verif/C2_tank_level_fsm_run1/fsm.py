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
        elif not (isinstance(source, (list, tuple))):
            self.sources = [source]
        else:
            self.sources = list(source)
        self.dest = dest
        if conditions is None:
            self.conditions = []
        elif not (isinstance(conditions, (list, tuple))):
            self.conditions = [conditions]
        else:
            self.conditions = list(conditions)
        if after is None:
            self.after = []
        elif not (isinstance(after, (list, tuple))):
            self.after = [after]
        else:
            self.after = list(after)

    def matches(self, state):
        _spt_r1 = self.sources is None or state in self.sources
        return _spt_r1


class Machine:
    """Minimal stand-in for transitions.Machine covering the usage in this file."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._transitions = {}

        model.state = initial

        for state_name in self.states:
            setattr(model, "is_" + state_name, self._make_is_state(model, state_name))

    def _make_is_state(self, model, state_name):
        def _is_state():
            _spt_r2 = model.state == state_name
            return _spt_r2
        return _is_state

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        t = _Transition(trigger, source, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(t)
        if not hasattr(self.model, trigger):
            setattr(self.model, trigger, self._make_trigger(trigger))

    def _make_trigger(self, trigger):
        def _trigger(*args, **kwargs):
            _spt_r3 = self._fire(trigger, *args, **kwargs)
            return _spt_r3
        return _trigger

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        transitions_for_trigger = self._transitions.get(trigger, [])
        matching_sources = [t for t in transitions_for_trigger if t.matches(current)]

        if not matching_sources:
            raise MachineError(
                "Can't trigger event {} from state {}!".format(trigger, current)
            )

        for t in matching_sources:
            ok = True
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
            for after in t.after:
                if not (isinstance(after, str)):
                    fn = after
                else:
                    fn = getattr(model, after)
                fn(*args, **kwargs)
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
