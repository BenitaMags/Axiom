"""Tests for tank_level_fsm (dual-mode).

The alarm/process logic is tested without the `transitions` library; the full state
machine is tested only when `transitions` is installed (skipped otherwise).
"""
from alarms import HIGH, HIHI, evaluate, interlock_ok
from process import Process


def test_alarm_bands():
    assert evaluate(0.1) == {"LOLO", "LOW"}
    assert evaluate(5.0) == set()
    assert evaluate(HIGH) == {"HIGH"}
    assert evaluate(HIHI) == {"HIHI", "HIGH"}


def test_interlock_blocks_above_hihi():
    assert interlock_ok(9.0)
    assert not interlock_ok(HIHI)


def test_process_fill_and_drain():
    p = Process(level=1.0, inlet=True)
    assert p.apply() == 1.5
    p.inlet, p.outlet = False, True
    assert abs(p.apply() - 1.1) < 1e-9


def _has_transitions():
    try:
        import transitions  # noqa: F401
        return True
    except ImportError:
        return False


def test_machine_fill_hold_drain_cycle():
    if not _has_transitions():
        print("skip test_machine_fill_hold_drain_cycle: 'transitions' not installed")
        return
    from fsm import TankController

    c = TankController(high=8.0, low=1.0)
    c.start_fill()
    assert c.state == "FILLING" and c.inlet is True
    c.step(8.0)  # reaches high setpoint
    assert c.state == "HOLDING" and c.inlet is False
    c.start_drain()
    assert c.state == "DRAINING" and c.outlet is True
    c.step(1.0)  # drained
    assert c.state == "IDLE" and c.outlet is False


def test_machine_interlock_trips_to_fault():
    if not _has_transitions():
        print("skip test_machine_interlock_trips_to_fault: 'transitions' not installed")
        return
    from fsm import TankController

    c = TankController()
    c.start_fill()
    c.step(9.6)  # above HIHI -> interlock trip
    assert c.state == "FAULT" and c.inlet is False and c.outlet is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
