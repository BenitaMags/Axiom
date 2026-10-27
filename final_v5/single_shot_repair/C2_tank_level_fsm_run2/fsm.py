# CHOSEN-TARGET: first-party
"""fsm -- finite-state controller for a fill/drain tank process.


FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping is implemented with a first-party state machine.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when a trigger cannot fire from the current state.

    Mirrors the behaviour of transitions.core.MachineError from the
    original `transitions`-based implementation: when a trigger is
    invoked and no transition matches the current state (or all
    matching transitions' guard conditions fail), an error is raised
    rather than the call silently doing nothing.
    """


class _Transition:
    """A single named transition definition."""

    def __init__(self, trigger, sources, dest, condition=None, action=None):
        self.trigger = trigger
        self.sources = sources
        self.dest = dest
        self.condition = condition
        self.action = action

    def matches(self, current_state):
        return self.sources == "*" or current_state in self.sources


class _StateMachine:
    """Minimal first-party finite-state machine engine.

    Supports named triggers that move a model between states, optional
    guard conditions, wildcard source states, and after-transition callbacks.
    If a trigger is invoked but no transition can fire (either because no
    transition's sources match the current state, or all matching
    transitions' guard conditions fail), a MachineError is raised --
    matching the default (non-ignoring) behaviour of the `transitions`
    library that this replaces.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self._transitions = {}
        model.state = initial

    def add_transition(self, trigger, sources, dest, conditions=None, after=None):
        if isinstance(sources, str) and sources != "*":
            sources = [sources]
        transition = _Transition(trigger, sources, dest, condition=conditions, action=after)
        self._transitions.setdefault(trigger, []).append(transition)
        setattr(self.model, trigger, self._make_trigger(trigger))

    def _make_trigger(self, trigger_name):
        def _trigger():
            current = self.model.state
            for transition in self._transitions.get(trigger_name, []):
                if not transition.matches(current):
                    continue
                if transition.condition is not None:
                    guard = getattr(self.model, transition.condition)
                    if not guard():
                        continue
                self.model.state = transition.dest
                if transition.action is not None:
                    action = getattr(self.model, transition.action)
                    action()
                return True
            raise MachineError(
                "Can't trigger event %s from state %s!" % (trigger_name, current)
            )
        return _trigger


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
