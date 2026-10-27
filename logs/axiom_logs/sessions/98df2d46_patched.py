"""fsm -- finite-state controller for a fill/drain tank process.

The state-machine engine is the substitution target: `transitions` -> a hand-rolled
FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `transitions` library in the original.
"""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class _Machine:
    """Minimal stand-in for transitions.Machine, bound to a model instance."""

    def __init__(self, model, states, initial):
        self.model = model
        self.states = list(states)
        self.transitions = {}
        model.state = initial
        model.machine = self

        def _bound_trigger(name):
            def trigger(*args, **kwargs):
                return self._fire(name)
            return trigger

        model_setattr = object.__setattr__
        self._triggers = set()

    def add_transition(self, trigger, source, dest, conditions=None, after=None):
        if isinstance(source, str):
            sources = [source]
        else:
            sources = list(source)

        if isinstance(conditions, str):
            conditions = [conditions]
        elif conditions is None:
            conditions = []

        if isinstance(after, str):
            after = [after]
        elif after is None:
            after = []

        self.transitions.setdefault(trigger, []).append({
            "sources": sources,
            "dest": dest,
            "conditions": conditions,
            "after": after,
        })

        if trigger not in self._triggers:
            self._triggers.add(trigger)
            self._bind_trigger(trigger)

    def _bind_trigger(self, trigger):
        model = self.model

        def fn(*args, **kwargs):
            return self._fire(trigger)

        setattr(model, trigger, fn)

    def _fire(self, trigger):
        model = self.model
        current = model.state
        specs = self.transitions.get(trigger, [])
        for spec in specs:
            sources = spec["sources"]
            if sources != ["*"] and current not in sources:
                continue

            ok = True
            for cond_name in spec["conditions"]:
                cond = getattr(model, cond_name)
                if not cond():
                    ok = False
                    break
            if not ok:
                continue

            model.state = spec["dest"]

            for after_name in spec["after"]:
                after_fn = getattr(model, after_name)
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
