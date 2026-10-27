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


class Machine:
    """Minimal stand-in for transitions.Machine, covering the subset used here.

    Supports add_transition with string or list source states, "*" wildcard
    source, optional conditions and after callback, and installs trigger
    methods plus a `state` attribute on the model.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.model.state = initial
        self._transitions = {}
        setattr(self.model, "machine", self)

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = list(self.states)
        elif not (isinstance(source, (list, tuple))):
            sources = [source]
        else:
            sources = list(source)

        if conditions is None:
            cond_names = []
        elif not (isinstance(conditions, (list, tuple))):
            cond_names = [conditions]
        else:
            cond_names = list(conditions)

        if after is None:
            after_names = []
        elif not (isinstance(after, (list, tuple))):
            after_names = [after]
        else:
            after_names = list(after)

        self._transitions.setdefault(trigger, [])
        for src in sources:
            self._transitions[trigger].append(
                {"source": src, "dest": dest, "conditions": cond_names, "after": after_names}
            )

        if not hasattr(self.model, trigger) or not callable(getattr(self.model, trigger, None)) \
                or getattr(getattr(self.model, trigger, None), "_is_fsm_trigger", False):
            self._install_trigger(trigger)

    def _install_trigger(self, trigger):
        machine = self

        def trigger_fn(*args, **kwargs):
            _spt_r1 = machine._fire(trigger, *args, **kwargs)
            return _spt_r1

        trigger_fn._is_fsm_trigger = True
        setattr(self.model, trigger, trigger_fn)

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        candidates = self._transitions.get(trigger, [])
        any_source_match = False
        for t in candidates:
            if t["source"] != current:
                continue
            any_source_match = True
            conds_ok = True
            for cname in t["conditions"]:
                cond = getattr(model, cname)
                if not cond():
                    conds_ok = False
                    break
            if not conds_ok:
                continue
            model.state = t["dest"]
            for aname in t["after"]:
                after_fn = getattr(model, aname)
                after_fn()
            return True
        if not any_source_match:
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
