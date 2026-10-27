"""Tests for boiler_control_loop (dual-mode). Fake Modbus client; DI control."""
from control import BoilerControl
from supervisor import BoilerSupervisor
from transport import ModbusIO


class FakeResult:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error


class FakeModbus:
    """Serves temperature/pressure registers and records valve writes."""
    def __init__(self, temp_deci, press_centibar):
        self.regs = {0x0000: temp_deci, 0x0001: press_centibar}
        self.writes = []

    def read_holding_registers(self, address, count, unit=1):
        return FakeResult([self.regs[address]])

    def write_register(self, address, value, unit=1):
        self.writes.append((address, value))
        return FakeResult()


def test_control_drives_valve_open_when_cold():
    c = BoilerControl(setpoint_c=80.0, kp=2.0, ki=0.0)
    assert c.step(60.0, 5.0) == 40.0  # 2.0 * (80-60), no integral


def test_overpressure_trips_and_latches():
    c = BoilerControl(max_pressure_bar=10.0)
    assert c.step(60.0, 10.5) == 0.0   # trip
    assert c.tripped
    assert c.step(60.0, 1.0) == 0.0    # stays closed after pressure recovers


def test_anti_windup_clamps_integral():
    c = BoilerControl(setpoint_c=100.0, kp=0.0, ki=1.0, i_limit=5.0)
    for _ in range(100):
        c.step(0.0, 1.0)  # huge persistent error
    assert c.integral == 5.0           # clamped, not unbounded


def test_supervisor_cycle_reads_controls_actuates():
    io = ModbusIO(client=FakeModbus(temp_deci=600, press_centibar=500))  # 60.0C, 5.0bar
    sup = BoilerSupervisor(io, control=BoilerControl(setpoint_c=80.0, kp=2.0, ki=0.0))
    rec = sup.cycle()
    assert rec["temperature"] == 60.0 and rec["pressure"] == 5.0
    assert rec["valve_pct"] == 40.0
    assert rec["valve_counts"] == int(round(40.0 * 40.95))
    assert io.client.writes[0][0] == 0x0010  # wrote the valve register


def test_supervisor_trips_on_overpressure():
    io = ModbusIO(client=FakeModbus(temp_deci=600, press_centibar=1100))  # 11.0 bar
    sup = BoilerSupervisor(io)
    rec = sup.cycle()
    assert rec["tripped"] and rec["valve_pct"] == 0.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
