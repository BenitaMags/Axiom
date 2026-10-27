# CHOSEN-TARGET: python-statemachine
"""fsm -- finite-state controller for a fill/drain tank process.


FSM. The transition guards, valve commands, and interlocks are first-party logic that
the LMA must re-encode into an equivalent but structurally different machine -- hence
Category C. State bookkeeping uses the `python-statemachine` library in this version.
"""
from alarms import interlock_ok
from statemachine import StateMachine, State

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class _TankMachine(StateMachine):
    IDLE = State("IDLE", initial=True)
    FILLING = State("FILLING")
    HOLDING = State("HOLDING")
    DRAINING = State("DRAINING")
    FAULT = State("FAULT")

    start_fill = IDLE.to(FILLING, cond="can_fill")
    level_reached = FILLING.to(HOLDING)
    start_drain = HOLDING.to(DRAINING) | FILLING.to(DRAINING)
    empty = DRAINING.to(IDLE)
    trip = (
        IDLE.to(FAULT)
        | FILLING.to(FAULT)
        | HOLDING.to(FAULT)
        | DRAINING.to(FAULT)
        | FAULT.to(FAULT)
    )

    def __init__(self, controller):
        self._controller = controller
        super().__init__()

    def can_fill(self):
        return self._controller.can_fill()

    def on_start_fill(self):
        self._controller.open_inlet()

    def on_level_reached(self):
        self._controller.close_inlet()

    def on_start_drain(self):
        self._controller.open_outlet()

    def on_empty(self):
        self._controller.close_outlet()

    def on_trip(self):
        self._controller.safe_all()


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0

        self._machine = _TankMachine(self)

    @property
    def state(self):
        return self._machine.current_state.id

    # --- transition triggers (public API preserved) ---------------------------
    def start_fill(self):
        self._machine.start_fill()

    def level_reached(self):
        self._machine.level_reached()

    def start_drain(self):
        self._machine.start_drain()

    def empty(self):
        self._machine.empty()

    def trip(self):
        self._machine.trip()

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
