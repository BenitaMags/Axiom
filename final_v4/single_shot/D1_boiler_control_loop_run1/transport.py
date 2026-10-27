# CHOSEN-TARGET: umodbus
"""transport -- Modbus field I/O for the boiler supervisor.

Uses `umodbus` (a pure-python, actively-maintained Modbus TCP/RTU library) in
place of `pymodbus`. umodbus exposes free functions that build raw ADU byte
strings (`umodbus.client.tcp.read_holding_registers` /
`write_single_register`) plus a `send_message(adu, sock)` helper that sends
the request over a socket and parses the response, raising an exception on
error rather than returning an error-flagged response object. This module
adapts that lower-level, socket-based API to the same read/write surface the
supervisor expects, with a dependency-injected client object.
"""
import socket


class _DefaultClient:
    """Adapter exposing the pymodbus-like read/write calls on top of umodbus."""

    def __init__(self, host: str, port: int = 502):
        from umodbus.client import tcp

        self._tcp = tcp
        self._sock = socket.create_connection((host, port))

    def read_holding_registers(self, address, count, unit=1):
        adu = self._tcp.read_holding_registers(
            slave_id=unit, starting_address=address, quantity=count
        )
        registers = self._tcp.send_message(adu, self._sock)
        return registers

    def write_register(self, address, value, unit=1):
        adu = self._tcp.write_single_register(
            slave_id=unit, address=address, value=value
        )
        self._tcp.send_message(adu, self._sock)
        return value


def _default_client(host: str, port: int = 502):
    return _DefaultClient(host, port=port)


class ModbusIO:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read1(self, address: int) -> int:
        try:
            registers = self.client.read_holding_registers(address, 1, unit=self.unit)
        except Exception as exc:
            raise IOError(f"Modbus read failed @ {address:#06x}") from exc
        return registers[0]

    def read_temperature(self, address: int = 0x0000) -> float:
        return self._read1(address) / 10.0  # deci-degrees C -> C

    def read_pressure(self, address: int = 0x0001) -> float:
        return self._read1(address) / 100.0  # centibar -> bar

    def write_valve(self, percent: float, address: int = 0x0010) -> int:
        counts = int(round(max(0.0, min(100.0, percent)) * 40.95))  # 0-100% -> 0-4095
        try:
            self.client.write_register(address, counts, unit=self.unit)
        except Exception as exc:
            raise IOError(f"Modbus write failed @ {address:#06x}") from exc
        return counts
