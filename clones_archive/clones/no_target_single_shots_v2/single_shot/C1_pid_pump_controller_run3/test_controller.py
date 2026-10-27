"""Tests for pid_pump_controller (dual-mode). Uses an injected fake PID."""
from controller import PumpController
from io_map import counts_to_level, speed_to_counts
from plant import Tank


class FakePID:
    """A proportional stand-in for simple_pid.PID (callable, has .setpoint)."""
    def __init__(self, kp):
        self.kp = kp
        self.setpoint = 0.0

    def __call__(self, level):
        return self.kp * (self.setpoint - level)


def test_io_scaling_roundtrip():
    assert counts_to_level(0) == 0.0
    assert counts_to_level(4095) == 10.0
    assert speed_to_counts(50.0) == 2048
    assert speed_to_counts(150.0) == 4095  # clamped


def test_setpoint_ramps_toward_target():
    c = PumpController(setpoint=5.0, ramp=0.5, pid=FakePID(1.0))
    c.update(0.0)
    assert c.applied_setpoint == 0.5
    c.update(0.0)
    assert c.applied_setpoint == 1.0


def test_output_is_clamped_to_valid_range():
    c = PumpController(setpoint=10.0, ramp=10.0, pid=FakePID(1000.0))
    # Huge error * huge gain would overflow; must clamp to full-scale counts.
    assert c.update(0.0) == speed_to_counts(100.0)


def test_manual_mode_commands_zero():
    c = PumpController(setpoint=5.0, pid=FakePID(1.0))
    c.mode = "MANUAL"
    assert c.update(0.0) == speed_to_counts(0.0)


def test_closed_loop_converges_to_setpoint():
    c = PumpController(setpoint=4.0, ramp=1.0, pid=FakePID(8.0))
    tank = Tank(level=0.0)
    level = 0.0
    for _ in range(300):
        counts = c.update(level)
        level = tank.step(100.0 * counts / 4095.0)
    assert abs(level - 4.0) < 0.25  # settles near the setpoint


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
