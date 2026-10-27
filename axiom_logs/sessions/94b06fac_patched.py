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


class _Machine:
    """Minimal stand-in for transitions.Machine covering the API used here."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.initial = initial
        model.state = initial

        self._triggers = {}

        def _is_state(s, current):
            if s == "*":
                return True
            if isinstance(s, (list, tuple)):
                return current in s
            return current == s

        self._is_state = _is_state

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        model = self.model
        self._triggers.setdefault(trigger, []).append((source, dest, conditions, after))

        def _make_trigger(trigger_name):
            def _trigger(*args, **kwargs):
                current = model.state
                specs = self._triggers[trigger_name]
                matched_source = False
                for (source, dest, conditions, after) in specs:
                    if not self._is_state(source, current):
                        continue
                    matched_source = True
                    if conditions is not None:
                        cond_names = conditions
                        if not isinstance(cond_names, (list, tuple)):
                            cond_names = [cond_names]
                        ok = True
                        for cond_name in cond_names:
                            cond_fn = getattr(model, cond_name)
                            if not cond_fn():
                                ok = False
                                break
                        if not ok:
                            continue
                    model.state = dest
                    if after is not None:
                        after_names = after
                        if not isinstance(after_names, (list, tuple)):
                            after_names = [after_names]
                        for after_name in after_names:
                            getattr(model, after_name)()
                    return True
                if matched_source:
                    return False
                raise MachineError(
                    "Can't trigger event %s from state %s!" % (trigger_name, current)
                )
            return _trigger

        bound = _make_trigger(trigger)
        setattr(model, trigger, bound)


class Machine(_Machine):
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
