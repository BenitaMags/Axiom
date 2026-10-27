# CHOSEN-TARGET: first-party
"""fsm -- finite-state controller for a fill/drain tank process.


FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping is implemented as first-party code (no external state
machine library is used).
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class _Transition:
    """A single named transition definition: sources -> destination."""

    __slots__ = ("trigger", "sources", "dest", "condition", "after")

    def __init__(self, trigger, sources, dest, condition=None, after=None):
        self.trigger = trigger
        self.sources = sources
        self.dest = dest
        self.condition = condition
        self.after = after

    def matches(self, state):
        return self.sources == "*" or state in self.sources


class _StateMachine:
    """Minimal first-party finite-state machine engine.

    Supports named triggers that move a model between states, optional
    guard conditions, and optional after-callbacks, mirroring the subset
    of behaviour this controller relies on.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._transitions = {}
        model.state = initial

    def add_transition(self, trigger, sources, dest, conditions=None, after=None):
        if isinstance(sources, str) and sources != "*":
            sources = [sources]
        condition_fn = None
        if conditions is not None:
            condition_fn = getattr(self.model, conditions)
        after_fn = None
        if after is not None:
            after_fn = getattr(self.model, after)

        transition = _Transition(trigger, sources, dest, condition_fn, after_fn)
        self._transitions.setdefault(trigger, []).append(transition)

        def _make_trigger(name):
            def _trigger():
                return self._fire(name)
            return _trigger

        setattr(self.model, trigger, _make_trigger(trigger))

    def _fire(self, trigger):
        current = self.model.state
        for transition in self._transitions.get(trigger, []):
            if not transition.matches(current):
                continue
            if transition.condition is not None and not transition.condition():
                continue
            self.model.state = transition.dest
            if transition.after is not None:
                transition.after()
            return True
        return False


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0

        self.machine = _StateMachine(model=self, states=STATES, initial="IDLE")
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
