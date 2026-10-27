"""C1/C2 oracle probes must exercise the dependency the migration replaces.

Before this, nothing in the corpus specified either engine's behaviour:

  C1  All five tests in test_controller.py inject a FakePID, and the probe did
      too, so `_make_pid` -- the only code the migration touches -- was never
      called by anything. Layer 5 does not reach it either: it looks for
      factories named `_default_*`. A first-party PID could compute anything.

  C2  The probe constructed the real TankController but never called
      `c.step(level)` and never wired the valve commands into the process, so
      the recorded golden was forty identical rows of `[0.0, "IDLE", True]`.
      It asserted that a state machine does not move.

These tests pin the discrimination that was added: a correct reimplementation
passes, and three specific wrong ones fail. They are slow (each spawns the
oracle's subprocesses) but they are the only check that the oracle can see the
code it certifies.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "equivalence"))

CORPUS = os.path.expanduser("~/Downloads/evaluation")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(CORPUS), reason="evaluation corpus not present")


def _oracle(name):
    from oracle import ProgramOracle
    return ProgramOracle(os.path.join(CORPUS, name), name)


# ── C1 ───────────────────────────────────────────────────────────────────────

# A faithful-enough PID: proportional + integral, the 0.01 s sample_time hold,
# and `_last_time` initialised in the constructor so the first call sees a
# near-zero dt. The first two of those were missing from the original version of
# this control, which therefore failed the timing invariants added after a blind
# LLM review found all three live reimplementations diverging on them. A positive
# control that is not actually faithful silently weakens every negative control
# below it, so it is worth spelling out in full.
C1_PID = '''import time as _time


class PID:
    def __init__(self, Kp=1.0, Ki=0.0, Kd=0.0, setpoint=0.0,
                 output_limits=(None, None), sample_time=0.01):
        self.Kp, self.Ki, self.Kd = Kp, Ki, Kd
        self.setpoint = setpoint
        self.output_limits = output_limits
        self.sample_time = sample_time
        self._integral = 0.0
        self._last_output = None
        self._last_input = None
        self._last_time = _time.monotonic()      # as simple-pid does, in reset()

    def _clamp(self, value):
        lo, hi = self.output_limits
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def __call__(self, input_, dt=None):
        now = _time.monotonic()
        if dt is None:
            dt = (now - self._last_time) or 1e-16
        # The hold: gated on _last_output, so the first call always computes.
        if (self.sample_time is not None and dt < self.sample_time
                and self._last_output is not None):
            return self._last_output
        error = self.setpoint - input_
        # Derivative on MEASUREMENT, which is simple-pid's default. Computing
        # it on error instead is invisible at a fixed setpoint and wrong the
        # moment the setpoint moves, so the control has to get this right or
        # the negative control below proves nothing.
        d_input = input_ - (self._last_input
                            if self._last_input is not None else input_)
        self._integral = self._clamp(self._integral + self.Ki * error * dt)
        derivative = -self.Kd * d_input / dt
        out = float(self._clamp(self.Kp * error + self._integral + derivative))
        self._last_output = out
        self._last_input = input_
        self._last_time = now
        return out


def _make_pid(kp, ki, kd, setpoint):
    return PID(kp, ki, kd, setpoint=setpoint, output_limits=(0.0, 100.0))'''


def _break(source, old, new):
    """`str.replace` that refuses to no-op.

    A negative control built with a plain `.replace()` whose pattern stops
    matching silently becomes a copy of the positive control, and then asserts
    that correct code is rejected -- which fails in a way that looks like an
    oracle bug. That happened here when C1_PID gained a `_clamp` helper: the
    output-limits control reverted to testing the faithful implementation.
    """
    assert old in source, f"negative control pattern no longer matches: {old!r}"
    out = source.replace(old, new)
    assert out != source
    return out


def _c1_variant(body):
    src = open(os.path.join(CORPUS, "C1_pid_pump_controller", "controller.py")).read()
    out = re.sub(
        r"def _make_pid\(kp, ki, kd, setpoint\):\n"
        r"    from simple_pid import PID  # lazy import for testability\n\n"
        r"    return PID\(kp, ki, kd, setpoint=setpoint, output_limits=\(0\.0, 100\.0\)\)",
        body, src)
    assert out != src, "the C1 factory shape changed; update this fixture"
    return {"controller": out}


def test_c1_accepts_a_correct_first_party_pid():
    assert _oracle("C1_pid_pump_controller").check_many(_c1_variant(C1_PID)).equivalent


def test_c1_rejects_an_inverted_sign_convention():
    bad = _break(C1_PID, "self.setpoint - input_", "input_ - self.setpoint")
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(bad))
    assert not r.equivalent and "engine_proportional" in r.detail


def test_c1_rejects_ignored_output_limits():
    # The limits now live in `_clamp`, which the integral path also uses, so
    # neutralising the helper is what "ignores output_limits" means here.
    bad = _break(C1_PID,
                 "    def _clamp(self, value):",
                 "    def _clamp(self, value):\n        return value\n        # noqa")
    assert not _oracle("C1_pid_pump_controller").check_many(_c1_variant(bad)).equivalent


def test_c1_probe_actually_constructs_the_engine():
    """A tripwire in the factory must be reached. This is the regression that
    matters: if a future edit reverts to injecting a fake, this fails."""
    tripwire = ('def _make_pid(kp, ki, kd, setpoint):\n'
                '    raise AssertionError("the PID engine was constructed")')
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(tripwire))
    assert not r.equivalent and "constructed" in r.detail


# ── C2 ───────────────────────────────────────────────────────────────────────

C2_HEAD = '''"""fsm -- finite-state controller for a fill/drain tank process."""
from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high = high
        self.low = low
        self.inlet = False
        self.outlet = False
        self._level = 0.0
        self.state = "IDLE"

    def _fire(self, event):
        if event == "trip":
            self.state = "FAULT"
            self.safe_all()
            return
{latch}
        table = {{
            ("IDLE", "start_fill"): ("FILLING", self.open_inlet),
            ("FILLING", "level_reached"): ("HOLDING", self.close_inlet),
            ("HOLDING", "start_drain"): ("DRAINING", self.open_outlet),
            ("FILLING", "start_drain"): ("DRAINING", self.open_outlet),
            ("DRAINING", "empty"): ("IDLE", self.close_outlet),
        }}
        key = (self.state, event)
        if key not in table:
            # `transitions` raises MachineError here. Returning instead makes an
            # invalid command silently succeed, which the oracle rejects -- and
            # this control has to get it right or every negative control below
            # it is being compared against an already-broken machine.
            raise RuntimeError("Can't trigger event %s from state %s!"
                               % (event, self.state))
{guard}
        self.state, action = table[key]
        action()

    def start_fill(self):    self._fire("start_fill")
    def level_reached(self): self._fire("level_reached")
    def start_drain(self):   self._fire("start_drain")
    def empty(self):         self._fire("empty")
    def trip(self):          self._fire("trip")

'''
C2_GUARD = ('        if event == "start_fill" and not self.can_fill():\n'
            '            return')
C2_UNLATCH = '        if self.state == "FAULT":\n            self.state = "IDLE"'


def _c2_variant(latch="", guard=C2_GUARD):
    from smart_loader.core.migration_finalize import remaining_source_imports
    src = open(os.path.join(CORPUS, "C2_tank_level_fsm", "fsm.py")).read()
    marker = "    # --- first-party guards and commands"
    assert marker in src, "the C2 file shape changed; update this fixture"
    variant = C2_HEAD.format(latch=latch, guard=guard) + marker + src.split(marker, 1)[1]
    assert remaining_source_imports(variant, "transitions") == []
    return {"fsm": variant}


def test_c2_accepts_a_correct_hand_rolled_fsm():
    assert _oracle("C2_tank_level_fsm").check_many(_c2_variant()).equivalent


def test_c2_rejects_a_trip_that_does_not_latch():
    """Checked with an EVENT, not another step(): after a trip, step() takes
    no branch and fires nothing, so any machine appears to latch."""
    r = _oracle("C2_tank_level_fsm").check_many(_c2_variant(latch=C2_UNLATCH))
    assert not r.equivalent and "latches_on_event" in r.detail


def test_c2_rejects_a_dropped_interlock_guard():
    """Checked where the guard bites. The main trace starts filling from an
    empty tank, where can_fill() is true anyway."""
    r = _oracle("C2_tank_level_fsm").check_many(_c2_variant(guard=""))
    assert not r.equivalent and "guard_blocks_fill" in r.detail


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── the engine timing invariants ──────────────────────────────────────────────
#
# Added after a blind LLM review returned MAJOR on all three live C1
# reimplementations while the oracle passed them. The golden vectors pin the
# engine with Ki=0/Kd=0 and a fresh instance per input -- deliberately, to stay
# reproducible -- which removes the clock and with it the two behaviours that
# dominate this library in the program, which runs Ki=0.5.

def test_c1_rejects_an_engine_with_no_sample_time_hold():
    """simple-pid returns the previous output for calls inside 0.01 s."""
    bad = _break(
        C1_PID,
        """        if (self.sample_time is not None and dt < self.sample_time
                and self._last_output is not None):
            return self._last_output
""",
        "")
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(bad))
    assert not r.equivalent
    assert "sample_time window" in r.detail


def test_c1_rejects_a_full_second_first_call_dt():
    """The `dt = 1.0` first-call substitution one live rewrite actually made.

    With Ki=0.5 and error=6.0 it injects +3.0 on the very first command.
    """
    bad = _break(C1_PID,
                 "self._last_time = _time.monotonic()      # as simple-pid does, in reset()",
                 "self._last_time = None")
    bad = _break(bad,
                 "            dt = (now - self._last_time) or 1e-16",
                 "            dt = 1.0 if self._last_time is None else "
                 "((now - self._last_time) or 1e-16)")
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(bad))
    assert not r.equivalent


def test_the_pristine_original_still_passes_every_layer():
    """The invariants must not reject the program they were written against."""
    src = open(os.path.join(CORPUS, "C1_pid_pump_controller",
                            "controller.py")).read()
    r = _oracle("C1_pid_pump_controller").check_many({"controller": src})
    assert r.equivalent, r.detail


def test_c1_rejects_a_derivative_computed_on_error():
    """simple-pid defaults `differential_on_measurement=True`.

    `Kd * d_error / dt` equals `-Kd * d_input / dt` while the setpoint is fixed,
    so this is invisible until the setpoint moves -- which this program does
    every cycle through `_ramp_setpoint`. Two live reimplementations got it
    wrong and passed every other layer.
    """
    bad = _break(
        C1_PID,
        """        d_input = input_ - (self._last_input
                            if self._last_input is not None else input_)""",
        """        d_input = -(error - (self._last_err
                             if getattr(self, '_last_err', None) is not None
                             else error))
        self._last_err = error""")
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(bad))
    assert not r.equivalent
    assert "on error, not on measurement" in r.detail


def test_c1_rejects_a_missing_zero_elapsed_time_guard():
    """simple-pid substitutes 1e-16 when the clock does not advance.

    `dt = now - last if (now - last) else 1e-16`. Without the fallback, a clock
    returning the same value twice makes the derivative divide by zero. Found by
    the blind judge on a rewrite that had already passed the other three
    invariants.
    """
    bad = _break(C1_PID,
                 "            dt = (now - self._last_time) or 1e-16",
                 "            dt = now - self._last_time")
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(bad))
    assert not r.equivalent
    assert "clock does not advance" in r.detail


def test_c1_accepts_a_none_initialised_previous_time():
    """Starting `_last_time` at None is NOT a defect, and must not be failed.

    Two live rewrites set `_last_time = None` and special-case the first call
    instead of seeding the clock in the constructor. The zero-delta path is then
    unreachable -- later calls hit the sample_time hold and return the cached
    output before any division -- and the first output is still Kp*error. An
    earlier version of this check fabricated the defect by assigning a float to
    `_last_time` by hand, producing a state the code never creates itself.
    """
    variant = _break(
        C1_PID,
        "        self._last_time = _time.monotonic()      # as simple-pid does, in reset()",
        "        self._last_time = None")
    variant = _break(
        variant,
        "            dt = (now - self._last_time) or 1e-16",
        "            dt = (1e-16 if self._last_time is None\n"
        "                  else (now - self._last_time) or 1e-16)")
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(variant))
    assert r.equivalent, r.detail


def test_c1_does_not_require_a_time_fn_attribute():
    """`time_fn` is library surface the program never uses.

    Failing a rewrite for lacking it would import an out-of-surface requirement,
    which is the mistake this file exists to avoid -- the same reason the
    invariants do not demand `reset`, `auto_mode`, `tunings` or `components`.
    """
    assert "time_fn" not in C1_PID, (
        "the positive control defines time_fn, so this test proves nothing"
    )
    r = _oracle("C1_pid_pump_controller").check_many(_c1_variant(C1_PID))
    assert r.equivalent, r.detail


def test_the_positive_control_matches_the_real_library_under_fuzzing():
    """Differential test against the installed library, not hand-picked cases.

    Every C1 defect this session was found by a human or a model noticing one
    specific behaviour, and each time the previous round's probes could not see
    the next one. A differential fuzz generalises past that: random gains, random
    inputs, a moving setpoint, and varied dt, compared trace-for-trace against
    `simple_pid`. Explicit dt removes the clock, so it is deterministic.

    This is the check that would have caught derivative-on-error, the
    first-call dt and the sample_time hold without anyone having to think of
    them. The control is P+I+D-on-measurement, so it is expected to match.
    """
    import random
    pytest = __import__("pytest")
    try:
        import simple_pid
    except ImportError:
        pytest.skip("simple-pid not installed")

    namespace: dict = {}
    exec(compile(C1_PID, "<control>", "exec"), namespace)
    make_control = namespace["_make_pid"]

    def trace(engine, steps):
        out = []
        for value, setpoint, dt in steps:
            engine.setpoint = setpoint
            out.append(round(float(engine(value, dt=dt)), 6))
        return out

    random.seed(20260912)
    for _ in range(120):
        kp = round(random.uniform(0.0, 5.0), 3)
        ki = round(random.uniform(0.0, 2.0), 3)
        kd = round(random.uniform(0.0, 1.0), 3)
        setpoint = round(random.uniform(-5, 15), 3)
        steps = [(round(random.uniform(-10, 20), 3),
                  round(random.uniform(-5, 15), 3),
                  random.choice([0.001, 0.01, 0.05, 0.1, 1.0])) for _ in range(8)]
        expected = trace(simple_pid.PID(kp, ki, kd, setpoint=setpoint,
                                        output_limits=(0.0, 100.0)), steps)
        actual = trace(make_control(kp, ki, kd, setpoint), steps)
        assert actual == expected, (
            f"control diverges at kp={kp} ki={ki} kd={kd}: "
            f"{actual[:4]} vs {expected[:4]}"
        )


# ── C2: invalid transitions ───────────────────────────────────────────────────

def _c2_sources(fsm_body=None):
    """The C2 module set, optionally with fsm.py replaced."""
    out = {}
    for name in ("alarms", "process", "fsm"):
        path = os.path.join(CORPUS, "C2_tank_level_fsm", f"{name}.py")
        if os.path.isfile(path):
            out[name] = open(path, encoding="utf-8").read()
    if fsm_body is not None:
        out["fsm"] = fsm_body
    return out


PERMISSIVE_FSM = '''from alarms import interlock_ok

STATES = ["IDLE", "FILLING", "HOLDING", "DRAINING", "FAULT"]

_ALLOWED = {
    "start_fill": (["IDLE"], "FILLING"),
    "level_reached": (["FILLING"], "HOLDING"),
    "start_drain": (["HOLDING", "FILLING"], "DRAINING"),
    "empty": (["DRAINING"], "IDLE"),
    "trip": (STATES, "FAULT"),
}


class TankController:
    def __init__(self, high: float = 8.0, low: float = 1.0):
        self.high, self.low = high, low
        self.inlet = self.outlet = False
        self._level = 0.0
        self.state = "IDLE"

    def can_fill(self):
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

    def _fire(self, event, after=None):
        sources, target = _ALLOWED[event]
        if self.state not in sources:
            return False                 # <-- silently ignored, the defect
        if event == "start_fill" and not self.can_fill():
            return False
        self.state = target
        if after:
            after()
        return True

    def start_fill(self):
        return self._fire("start_fill", self.open_inlet)

    def level_reached(self):
        return self._fire("level_reached", self.close_inlet)

    def start_drain(self):
        return self._fire("start_drain", self.open_outlet)

    def empty(self):
        return self._fire("empty", self.close_outlet)

    def trip(self):
        return self._fire("trip", self.safe_all)

    def step(self, level: float):
        self._level = level
        if not interlock_ok(level) and self.state != "FAULT":
            self.trip()
            return
        if self.state == "FILLING" and level >= self.high:
            self.level_reached()
        elif self.state == "DRAINING" and level <= self.low:
            self.empty()
'''


def test_c2_rejects_an_fsm_that_ignores_invalid_transitions():
    """`transitions` raises MachineError; silently returning is not equivalent.

    Found by differential fuzzing: 291 of 300 random event/step scripts diverged
    on three live rewrites, and discounting invalid-transition errors on both
    sides made all 300 match -- so this was their only behavioural gap.

    In scope because the program's own test suite calls these triggers directly.
    """
    r = _oracle("C2_tank_level_fsm").check_many(_c2_sources(PERMISSIVE_FSM))
    assert not r.equivalent
    assert "accepted silently" in r.detail


def test_c2_accepts_an_fsm_that_refuses_invalid_transitions():
    """The same machine, raising instead of returning False, must pass.

    Without this the test above would be satisfied by an oracle that rejects
    every hand-rolled FSM, which would prove nothing.
    """
    strict = PERMISSIVE_FSM.replace(
        "            return False                 # <-- silently ignored, the defect",
        "            raise RuntimeError(\n"
        "                \"Can't trigger event %s from state %s!\"\n"
        "                % (event, self.state))")
    assert strict != PERMISSIVE_FSM
    r = _oracle("C2_tank_level_fsm").check_many(_c2_sources(strict))
    assert r.equivalent, r.detail


def test_the_pristine_c2_original_still_passes():
    r = _oracle("C2_tank_level_fsm").check_many(_c2_sources())
    assert r.equivalent, r.detail


def test_the_c2_control_matches_the_real_library_under_fuzzing():
    """Differential test of the hand-rolled FSM against `transitions`.

    This is the check that found C2's only real gap in one shot, where C1 needed
    four rounds and two reviewers to reach the same confidence. Random event and
    step scripts, compared action-for-action against the pristine original (which
    uses the real library).

    The exception TYPE is not compared -- a first-party FSM cannot raise
    `transitions.MachineError` -- only WHETHER the call was refused, and the
    state and outputs afterwards.
    """
    import importlib.util
    import random
    pytest = __import__("pytest")
    try:
        import transitions                                    # noqa: F401
    except ImportError:
        pytest.skip("transitions not installed")

    strict = PERMISSIVE_FSM.replace(
        "            return False                 # <-- silently ignored, the defect",
        "            raise RuntimeError(\n"
        "                \"Can't trigger event %s from state %s!\"\n"
        "                % (event, self.state))")
    assert strict != PERMISSIVE_FSM

    corpus = os.path.join(CORPUS, "C2_tank_level_fsm")

    def load(fsm_source, tag):
        sys.path.insert(0, corpus)
        for name in ("alarms", "process"):
            path = os.path.join(corpus, f"{name}.py")
            if os.path.isfile(path):
                spec = importlib.util.spec_from_file_location(name, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                spec.loader.exec_module(mod)
        namespace = {"__name__": tag}
        exec(compile(fsm_source, f"<{tag}>", "exec"), namespace)
        return namespace["TankController"]

    events = ["start_fill", "level_reached", "start_drain", "empty", "trip"]

    def observe(controller_cls, script):
        c = controller_cls()
        seen = []
        for kind, arg in script:
            try:
                if kind == "step":
                    c.step(arg)
                else:
                    getattr(c, kind)()
                seen.append((c.state, c.inlet, c.outlet))
            except Exception:                                 # noqa: BLE001
                seen.append(("REFUSED", c.state, c.inlet, c.outlet))
        return seen

    original = load(open(os.path.join(corpus, "fsm.py"), encoding="utf-8").read(),
                    "c2_original")
    control = load(strict, "c2_control")

    random.seed(20260912)
    for _ in range(120):
        script = [("step", round(random.uniform(-2, 12), 3))
                  if random.random() < 0.55 else (random.choice(events), None)
                  for _ in range(10)]
        assert observe(control, script) == observe(original, script), script


def test_c2_rejects_an_fsm_that_raises_on_a_failed_guard():
    """The other half of the refusal behaviour.

    `transitions` raises only when no transition leaves the current state. When a
    transition matches but its `conditions` guard evaluates False it returns
    False. The first version of this invariant checked only the raising half, and
    two of three live rewrites then over-generalised it and raised on guard
    failure too -- a defect the invariant itself induced.
    """
    strict = PERMISSIVE_FSM.replace(
        "            return False                 # <-- silently ignored, the defect",
        "            raise RuntimeError(\n"
        "                \"Can't trigger event %s from state %s!\"\n"
        "                % (event, self.state))")
    # Now also raise when the guard fails -- the over-correction.
    over = _break(strict,
                  '        if event == "start_fill" and not self.can_fill():\n'
                  '            return False',
                  '        if event == "start_fill" and not self.can_fill():\n'
                  '            raise RuntimeError("guard failed")')
    r = _oracle("C2_tank_level_fsm").check_many(_c2_sources(over))
    assert not r.equivalent
    assert "guard failed" in r.detail or "can_fill guard" in r.detail


def test_c2_guard_failure_must_not_move_the_state():
    """A guard that returns False without blocking is equally wrong."""
    strict = PERMISSIVE_FSM.replace(
        "            return False                 # <-- silently ignored, the defect",
        "            raise RuntimeError(\n"
        "                \"Can't trigger event %s from state %s!\"\n"
        "                % (event, self.state))")
    leaky = _break(strict,
                   '        if event == "start_fill" and not self.can_fill():\n'
                   '            return False',
                   '        if event == "start_fill" and not self.can_fill():\n'
                   '            self.state = target\n'
                   '            return False')
    r = _oracle("C2_tank_level_fsm").check_many(_c2_sources(leaky))
    assert not r.equivalent
