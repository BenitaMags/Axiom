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

    def source_matches(self, state):
        _spt_r1 = self.source == "*" or state in self.source
        return _spt_r1


class Machine:
    """Minimal reimplementation of the subset of `transitions.Machine` used here."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.initial = initial
        self._transitions = {}  # trigger -> list of _Transition

        model.state = initial
        model.machine = self

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if isinstance(source, str):
            if not (source != "*"):
                source = "*"
            else:
                source = [source]
        if conditions is not None:
            if not isinstance(conditions, (list, tuple)):
                conditions = [conditions]
        if after is not None:
            if not isinstance(after, (list, tuple)):
                after = [after]

        transition = _Transition(trigger, source, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(transition)

        if not hasattr(self.model, trigger):
            def _make_trigger(name):
                def _trigger(*args, **kwargs):
                    _spt_r2 = self._fire(name, *args, **kwargs)
                    return _spt_r2
                return _trigger

            setattr(self.model, trigger, _make_trigger(trigger))

    def _fire(self, trigger, *args, **kwargs):
        transitions_for_trigger = self._transitions.get(trigger, [])
        current_state = self.model.state

        matched_any_source = False
        for transition in transitions_for_trigger:
            if not transition.source_matches(current_state):
                continue
            matched_any_source = True

            if transition.conditions:
                ok = True
                for cond in transition.conditions:
                    if not (isinstance(cond, str)):
                        method = cond
                    else:
                        method = getattr(self.model, cond)
                    if not method():
                        ok = False
                        break
                if not ok:
                    continue

            self.model.state = transition.dest

            if transition.after:
                for after in transition.after:
                    if not (isinstance(after, str)):
                        method = after
                    else:
                        method = getattr(self.model, after)
                    method()

            return True

        if not matched_any_source:
            raise MachineError(
                "Can't trigger event %s from state %s!" % (trigger, current_state)
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
