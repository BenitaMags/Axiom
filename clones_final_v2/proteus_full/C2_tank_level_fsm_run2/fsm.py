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


class _Machine:
    """Minimal stand-in for transitions.Machine, bound to a model object."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = states
        self.initial = initial
        self._transitions = {}
        model.state = initial
        model.machine = self

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        sources = list(source) if not (isinstance(source, str)) else [source]

        if conditions is None:
            cond_names = []
        elif not (isinstance(conditions, str)):
            cond_names = list(conditions)
        else:
            cond_names = [conditions]

        if after is None:
            after_names = []
        elif not (isinstance(after, str)):
            after_names = list(after)
        else:
            after_names = [after]

        self._transitions.setdefault(trigger, []).append({
            "sources": sources,
            "dest": dest,
            "conditions": cond_names,
            "after": after_names,
        })

        if not hasattr(self.model, trigger):
            def _make_trigger(trigger_name):
                def _trigger(*args, **kwargs):
                    _spt_r1 = self._fire(trigger_name, *args, **kwargs)
                    return _spt_r1
                return _trigger
            setattr(self.model, trigger, _make_trigger(trigger))

    def _fire(self, trigger, *args, **kwargs):
        defs = self._transitions.get(trigger, [])
        current = self.model.state
        matched_source = False
        for tdef in defs:
            sources = tdef["sources"]
            if "*" not in sources:
                if current not in sources:
                    continue
            matched_source = True
            conditions_ok = True
            for cond_name in tdef["conditions"]:
                cond_fn = getattr(self.model, cond_name)
                if not cond_fn():
                    conditions_ok = False
                    break
            if not conditions_ok:
                continue
            self.model.state = tdef["dest"]
            for after_name in tdef["after"]:
                after_fn = getattr(self.model, after_name)
                after_fn()
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
