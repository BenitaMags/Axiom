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
    """Minimal reimplementation of the subset of `transitions.Machine` used here.

    Supports a single model (passed via `model=`), string/list source states,
    wildcard "*" source state, `conditions` (name of a bound method returning
    truthy/falsy), and `after` callback (name of a bound method) invoked once
    the state has changed. Adds a bound trigger method to the model for each
    transition name, and exposes `state` as an attribute on the model.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._transitions = {}  # trigger name -> list of transition dicts

        model.machine = self
        model.state = initial

        # Attach a `trigger` attribute helper (not strictly required by usage
        # here, but keeps parity with transitions' Machine.trigger).
        def _trigger(name, *args, **kwargs):
            _spt_r1 = self._fire(name, *args, **kwargs)
            return _spt_r1

        model.trigger = _trigger

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = ["*"]
        elif not (isinstance(source, (list, tuple))):
            sources = [source]
        else:
            sources = list(source)

        if isinstance(conditions, (list, tuple)):
            cond_list = list(conditions)
        elif not (conditions is None):
            cond_list = [conditions]
        else:
            cond_list = []

        if isinstance(after, (list, tuple)):
            after_list = list(after)
        elif not (after is None):
            after_list = [after]
        else:
            after_list = []

        entry = {
            "sources": sources,
            "dest": dest,
            "conditions": cond_list,
            "after": after_list,
        }
        self._transitions.setdefault(trigger, []).append(entry)

        if not hasattr(self.model, trigger) or trigger not in getattr(
            self.model, "_fsm_triggers", set()
        ):
            if not hasattr(self.model, "_fsm_triggers"):
                self.model._fsm_triggers = set()
            self.model._fsm_triggers.add(trigger)

            def _bound_trigger(*args, _trigger_name=trigger, **kwargs):
                _spt_r2 = self._fire(_trigger_name, *args, **kwargs)
                return _spt_r2

            setattr(self.model, trigger, _bound_trigger)

    def _fire(self, trigger, *args, **kwargs):
        current = self.model.state
        candidates = self._transitions.get(trigger, [])

        matched_source = False
        for entry in candidates:
            if current in entry["sources"] or "*" in entry["sources"]:
                matched_source = True
                ok = True
                for cond_name in entry["conditions"]:
                    cond_fn = getattr(self.model, cond_name)
                    if not cond_fn():
                        ok = False
                        break
                if not ok:
                    continue
                self.model.state = entry["dest"]
                for after_name in entry["after"]:
                    after_fn = getattr(self.model, after_name)
                    after_fn()
                return True

        if not matched_source:
            raise MachineError(
                "Can't trigger event %s from state %s!" % (trigger, current)
            )
        return False


class Machine(_Machine):
    """Drop-in stand-in for `transitions.Machine` covering the used API."""
    pass


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
