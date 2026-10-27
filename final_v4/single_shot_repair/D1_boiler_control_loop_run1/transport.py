# CHOSEN-TARGET: umodbus
"""transport -- Modbus field I/O for the boiler supervisor.

Uses `umodbus` (a pure-python, actively-maintained Modbus TCP/RTU library) in
place of `pymodbus`. umodbus exposes free functions that build raw ADU byte
strings (`umodbus.client.tcp.read_holding_registers` /
`write_single_register`) plus a `send_message(adu, sock)` helper that sends
the request over a socket and parses the response. For reads,
`send_message` returns a plain list of register values (not a response
object with an `.isError()` method / `.registers` attribute like pymodbus),
and errors are signalled by raising an exception rather than returning an
error-flagged object.

`ModbusIO` is written against a small pymodbus-shaped surface
(`read_holding_registers(...)` returning an object with a `.registers` list,
and `write_register(...)` returning an object with `.isError()`), because
that is the seam the supervisor/tests are built around and because it lets
callers keep injecting simple fakes. `_DefaultClient` is the adapter that
bridges the real umodbus API to that shape.
"""
import socket


class _Result:
    """Minimal pymodbus-like result wrapper around a umodbus response."""

    def __init__(self, registers=None):
        self.registers = registers or []

    def isError(self) -> bool:
        return False


class _DefaultClient:
    """Adapter exposing pymodbus-like read/write calls on top of umodbus."""

    def __init__(self, host: str, port: int = 502):
        from umodbus.client import tcp

        self._tcp = tcp
        self._sock = socket.create_connection((host, port))

    def read_holding_registers(self, address, count, unit=1):
        adu = self._tcp.read_holding_registers(
            slave_id=unit, starting_address=address, quantity=count
        )
        registers = self._tcp.send_message(adu, self._sock)
        return _Result(registers=list(registers))

    def write_register(self, address, value, unit=1):
        adu = self._tcp.write_single_register(
            slave_id=unit, address=address, value=value
        )
        self._tcp.send_message(adu, self._sock)
        return _Result()


def _default_client(host: str, port: int = 502):
    return _DefaultClient(host, port=port)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: int) -> int:
        rr = self.client.read_holding_registers(address, 1, unit=self.unit)
        if rr.isError():
            raise IOError(f"Modbus read failed @ {address:#06x}")
        return rr.registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        wr = self.client.write_register(address, counts, unit=self.unit)
        if wr.isError():
            raise IOError(f"Modbus write failed @ {address:#06x}")
        return counts
