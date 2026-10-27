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
    """Minimal first-party stand-in for transitions.Machine."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = states
        self.initial = initial
        self._transitions = {}
        model.state = initial

        def is_state(state_name, model=model):
            _spt_r1 = model.state == state_name
            return _spt_r1

        model.is_state = is_state
        model.machine = self

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        sources = list(source) if not (isinstance(source, str)) else [source]

        if conditions is None:
            condition_names = []
        elif not (isinstance(conditions, str)):
            condition_names = list(conditions)
        else:
            condition_names = [conditions]

        if after is None:
            after_names = []
        elif not (isinstance(after, str)):
            after_names = list(after)
        else:
            after_names = [after]

        entry = {
            "sources": sources,
            "dest": dest,
            "conditions": condition_names,
            "after": after_names,
        }
        self._transitions.setdefault(trigger, []).append(entry)

        def trigger_fn(*args, _trigger=trigger, **kwargs):
            _spt_r2 = self._fire(_trigger, *args, **kwargs)
            return _spt_r2

        setattr(self.model, trigger, trigger_fn)

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        entries = self._transitions.get(trigger, [])

        matched_source = False
        for entry in entries:
            sources = entry["sources"]
            if "*" not in sources:
                if current not in sources:
                    continue
            matched_source = True
            conditions_ok = True
            for cond_name in entry["conditions"]:
                cond = getattr(model, cond_name)
                if not cond():
                    conditions_ok = False
                    break
            if not conditions_ok:
                continue
            model.state = entry["dest"]
            for after_name in entry["after"]:
                after_fn = getattr(model, after_name)
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
