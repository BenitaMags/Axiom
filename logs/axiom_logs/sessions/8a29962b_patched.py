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
        return self.source == "*" or state in self.source


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
            source = [source] if source != "*" else "*"
        if conditions is not None and not isinstance(conditions, (list, tuple)):
            conditions = [conditions]
        if after is not None and not isinstance(after, (list, tuple)):
            after = [after]

        transition = _Transition(trigger, source, dest, conditions=conditions, after=after)
        self._transitions.setdefault(trigger, []).append(transition)

        if not hasattr(self.model, trigger):
            def _make_trigger(name):
                def _trigger(*args, **kwargs):
                    return self._fire(name, *args, **kwargs)
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
                    method = getattr(self.model, cond) if isinstance(cond, str) else cond
                    if not method():
                        ok = False
                        break
                if not ok:
                    continue

            self.model.state = transition.dest

            if transition.after:
                for after in transition.after:
                    method = getattr(self.model, after) if isinstance(after, str) else after
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
