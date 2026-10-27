"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when a trigger cannot fire from the current state."""


class _Machine:
    """Minimal stand-in for transitions.Machine covering the API used here.

    Supports add_transition with source states (including "*" for any state
    and lists of states), conditions, and after callbacks. Binds trigger
    methods and a `state` attribute onto the given model.
    """

    def __init__(self, model, states, initial="IDLE"):
        self.model = model
        self.states = list(states)
        self._transitions = {}  # trigger -> list of (sources, dest, conditions, after)
        model.state = initial

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = list(self.states)
        elif isinstance(source, (list, tuple)):
            sources = list(source)
        else:
            sources = [source]

        if conditions is None:
            cond_list = []
        elif isinstance(conditions, (list, tuple)):
            cond_list = list(conditions)
        else:
            cond_list = [conditions]

        if after is None:
            after_list = []
        elif isinstance(after, (list, tuple)):
            after_list = list(after)
        else:
            after_list = [after]

        self._transitions.setdefault(trigger, []).append(
            (sources, dest, cond_list, after_list)
        )

        if not hasattr(self.model, trigger) or trigger not in getattr(
            self.model, "_bound_triggers", set()
        ):
            self._bind_trigger(trigger)

    def _bind_trigger(self, trigger):
        model = self.model
        if not hasattr(model, "_bound_triggers"):
            model._bound_triggers = set()
        model._bound_triggers.add(trigger)

        def _trigger_fn(*args, **kwargs):
            return self._fire(trigger, *args, **kwargs)

        setattr(model, trigger, _trigger_fn)

    def _fire(self, trigger, *args, **kwargs):
        model = self.model
        current = model.state
        entries = self._transitions.get(trigger, [])
        matched_source = False
        for sources, dest, cond_list, after_list in entries:
            if current in sources:
                matched_source = True
                for cond_name in cond_list:
                    cond_fn = getattr(model, cond_name)
                    if not cond_fn():
                        break
                else:
                    model.state = dest
                    for after_name in after_list:
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
