"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class MachineError(Exception):
    """Raised when a trigger is invoked from a state with no matching transition."""


class _Machine:
    """Minimal stand-in for transitions.Machine, tailored to this program's usage.

    Supports add_transition(trigger, source, dest, conditions=None, after=None)
    where source may be a single state name or a list of state names, or "*"
    to match any state. Installs trigger methods on the model that check
    conditions, perform the state change, and call the after callback.

    Mirrors transitions.Machine's behaviour of raising MachineError when a
    trigger is called from a state for which no transition exists.
    """

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.model.state = initial
        self._triggers = {}

        def is_state(state_name, model=model):
            return model.state == state_name

        model.is_state = is_state

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if source == "*":
            sources = list(self.states)
        elif isinstance(source, (list, tuple)):
            sources = list(source)
        else:
            sources = [source]

        if conditions is None:
            condition_names = []
        elif isinstance(conditions, (list, tuple)):
            condition_names = list(conditions)
        else:
            condition_names = [conditions]

        if after is None:
            after_names = []
        elif isinstance(after, (list, tuple)):
            after_names = list(after)
        else:
            after_names = [after]

        model = self.model

        # Accumulate all (sources, dest, conditions, after) tuples registered
        # for this trigger name, so we can decide, on call, whether the
        # current state has any matching transition at all.
        existing = self._triggers.setdefault(trigger, [])
        existing.append((sources, dest, condition_names, after_names))

        def make_trigger(trigger_name):
            def _trigger(*args, **kwargs):
                specs = self._triggers[trigger_name]
                matched_source = False
                for sources, dest, condition_names, after_names in specs:
                    if model.state not in sources:
                        continue
                    matched_source = True
                    conds_ok = True
                    for cname in condition_names:
                        cond = getattr(model, cname)
                        if not cond():
                            conds_ok = False
                            break
                    if not conds_ok:
                        continue
                    model.state = dest
                    for aname in after_names:
                        cb = getattr(model, aname)
                        cb()
                    return True
                if not matched_source:
                    raise MachineError(
                        "Can't trigger event %s from state %s!" % (trigger_name, model.state)
                    )
                return False
            return _trigger

        setattr(model, trigger, make_trigger(trigger))


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
