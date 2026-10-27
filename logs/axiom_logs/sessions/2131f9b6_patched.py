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


class _Transition:
    __slots__ = ("source", "dest", "conditions", "after")

    def __init__(self, source, dest, conditions=None, after=None):
        self.source = source
        self.dest = dest
        self.conditions = conditions or []
        self.after = after or []


class Machine:
    """Minimal re-implementation of the subset of `transitions.Machine` used here."""

    def __init__(self, model=None, states=None, initial=None):
        self.model = model
        self.states = list(states) if states is not None else []
        self.initial = initial
        # trigger name -> list of _Transition
        self._transitions = {}

        if model is not None:
            model.state = initial
            self._bind_model(model)

    def _bind_model(self, model):
        # Ensure "*" wildcard transitions are matched against any state.
        pass

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if conditions is None:
            conditions_list = []
        elif isinstance(conditions, (list, tuple)):
            conditions_list = list(conditions)
        else:
            conditions_list = [conditions]

        if after is None:
            after_list = []
        elif isinstance(after, (list, tuple)):
            after_list = list(after)
        else:
            after_list = [after]

        if source == "*":
            sources = ["*"]
        elif isinstance(source, (list, tuple)):
            sources = list(source)
        else:
            sources = [source]

        transition = _Transition(sources, dest, conditions_list, after_list)
        self._transitions.setdefault(trigger, []).append(transition)

        # Bind the trigger method onto the model, mimicking `transitions`.
        if self.model is not None:
            self._install_trigger(trigger)

    def _install_trigger(self, trigger):
        machine = self
        model = self.model

        def _trigger(*args, **kwargs):
            return machine._fire(trigger, model, *args, **kwargs)

        setattr(model, trigger, _trigger)

    def _fire(self, trigger, model, *args, **kwargs):
        transitions_list = self._transitions.get(trigger, [])
        current_state = model.state
        matched_source = False
        for transition in transitions_list:
            if "*" in transition.source or current_state in transition.source:
                matched_source = True
                ok = True
                for cond in transition.conditions:
                    fn = getattr(model, cond) if isinstance(cond, str) else cond
                    if not fn():
                        ok = False
                        break
                if not ok:
                    continue
                model.state = transition.dest
                for after in transition.after:
                    fn = getattr(model, after) if isinstance(after, str) else after
                    fn()
                return True
        raise MachineError(
            "Can't trigger event %s from state %s!" % (trigger, current_state)
        )


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
