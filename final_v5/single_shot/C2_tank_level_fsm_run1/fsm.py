# CHOSEN-TARGET: first-party
"""fsm -- finite-state controller for a fill/drain tank process.


FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping is implemented as first-party code (no `transitions`
dependency).
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class _Transition:
    """Describes one edge of the state machine."""

    __slots__ = ("sources", "dest", "condition", "action")

    def __init__(self, sources, dest, condition=None, action=None):
        # sources may be "*", a single state name, or a list of state names
        if sources == "*":
            self.sources = "*"
        elif isinstance(sources, (list, tuple, set)):
            self.sources = list(sources)
        else:
            self.sources = [sources]
        self.dest = dest
        self.condition = condition
        self.action = action

    def matches(self, current_state):
        return self.sources == "*" or current_state in self.sources


class _TinyMachine:
    """A minimal, purpose-built replacement for the parts of `transitions.Machine`
    that this controller relies on: trigger methods installed on a model, guarded
    transitions, wildcard sources, and after-callbacks."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._triggers = {}
        model.state = initial

    def add_transition(self, trigger, sources, dest, conditions=None, after=None):
        transition = _Transition(sources, dest, condition=conditions, action=after)
        self._triggers.setdefault(trigger, []).append(transition)
        self._install_trigger(trigger)

    def _install_trigger(self, trigger):
        def _fire(*args, **kwargs):
            return self._trigger(trigger, *args, **kwargs)

        setattr(self.model, trigger, _fire)

    def _trigger(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        for transition in self._triggers.get(trigger, []):
            if not transition.matches(current):
                continue
            if transition.condition is not None:
                guard = getattr(model, transition.condition)
                if not guard():
                    continue
            model.state = transition.dest
            if transition.action is not None:
                getattr(model, transition.action)()
            return True
        return False


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0

        self.machine = _TinyMachine(model=self, states=STATES, initial="IDLE")
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
