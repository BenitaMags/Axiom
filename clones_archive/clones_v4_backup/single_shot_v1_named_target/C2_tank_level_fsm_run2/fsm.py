"""fsm -- finite-state controller for a fill/drain tank process.

This module implements its own lightweight finite-state-machine engine
(instead of relying on the third-party `transitions` package). The engine
supports named states, triggers with optional guard/after callbacks, and a
wildcard source state ("*") the same way `transitions.Machine` does for the
subset of features used here.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]

WILDCARD_SOURCE = "*"


class _TransitionRule:
    """A single named edge in the state graph."""

    def __init__(self, trigger, sources, target, guard_name=None, after_name=None):
        self.trigger = trigger
        if isinstance(sources, (list, tuple, set)):
            self.sources = list(sources)
        else:
            self.sources = [sources]
        self.target = target
        self.guard_name = guard_name
        self.after_name = after_name

    def matches(self, current_state):
        return WILDCARD_SOURCE in self.sources or current_state in self.sources


class _StateEngine:
    """Minimal reimplementation of the pieces of `transitions.Machine` used here."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._rules_by_trigger = {}
        model.state = initial

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        rule = _TransitionRule(trigger, source, dest, guard_name=conditions, after_name=after)
        self._rules_by_trigger.setdefault(trigger, []).append(rule)
        self._bind_trigger(trigger)

    def _bind_trigger(self, trigger):
        engine = self

        def _fire(*args, **kwargs):
            return engine._invoke(trigger)

        setattr(self.model, trigger, _fire)

    def _invoke(self, trigger):
        current = self.model.state
        candidates = self._rules_by_trigger.get(trigger, [])
        for rule in candidates:
            if not rule.matches(current):
                continue
            if rule.guard_name is not None:
                guard_fn = getattr(self.model, rule.guard_name)
                if not guard_fn():
                    continue
            self.model.state = rule.target
            if rule.after_name is not None:
                after_fn = getattr(self.model, rule.after_name)
                after_fn()
            return True
        return False


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0

        self.machine = _StateEngine(model=self, states=STATES, initial="IDLE")
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
