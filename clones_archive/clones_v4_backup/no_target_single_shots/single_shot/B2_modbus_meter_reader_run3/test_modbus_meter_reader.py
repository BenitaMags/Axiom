"""Tests for modbus_meter_reader (dual-mode). Uses an injected fake Modbus client."""
import struct

from modbus_meter_reader import MeterReader
from registers import decode


def _words(fmt, value):
    raw = struct.pack(fmt, value)
    return [struct.unpack(">H", raw[i:i + 2])[0] for i in range(0, len(raw), 2)]


class FakeResult:
    def __init__(self, registers, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


class FakeClient:
    def __init__(self, by_addr, fail=None):
        self.by_addr = by_addr
        self.fail = fail or set()
        self.reads = []

    def read_holding_registers(self, address, count, unit=1):
        self.reads.append((address, count, unit))
        if address in self.fail:
            return FakeResult([], error=True)
        return FakeResult(self.by_addr[address])


def test_decode_float_and_uint():
    assert abs(decode("f32", _words(">f", 230.0), 1.0) - 230.0) < 1e-6
    assert decode("u32", _words(">I", 12345), 0.001) == 12.345


def test_reads_full_measurement():
    client = FakeClient({
        0x0000: _words(">f", 230.0),   # voltage
        0x0002: _words(">f", 5.0),     # current
        0x0004: _words(">f", 1150.0),  # power
        0x0006: _words(">I", 42000),   # energy Wh -> 42.0 kWh
    })
    rec = MeterReader(client=client, unit=3).read_measurement()
    assert abs(rec["voltage"] - 230.0) < 1e-6
    assert abs(rec["energy"] - 42.0) < 1e-6
    assert client.reads[0] == (0x0000, 2, 3)  # unit threaded through


def test_read_error_raises():
    client = FakeClient({0x0000: [0, 0]}, fail={0x0000})
    try:
        MeterReader(client=client).read_measurement()
        assert False, "expected IOError"
    except IOError:
        pass


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
