# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.

This version is built on top of `umodbus`, which exposes a much lower-level,
function-oriented API (plain socket handling plus free functions such as
`umodbus.client.tcp.read_holding_registers` / `send_message`). To keep the public
API of this module identical (a `MeterReader` class whose injected `client`
object exposes a pymodbus-like `read_holding_registers(address, count, unit=...)`
method returning an object with `.isError()` and `.registers`), we provide a thin
adapter class that wraps a plain TCP socket and speaks umodbus under the hood.
"""
import socket

from registers import REGISTERS, decode


class _ModbusReadResult:
    """Small pymodbus-compatible result wrapper used internally by the adapter."""

    def __init__(self, registers=None, error=None):
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class _UModbusTcpClientAdapter:
    """Adapts umodbus's function-based TCP client API to the pymodbus-like
    interface expected by MeterReader (``read_holding_registers(address,
    count, unit=...)`` returning an object with ``isError()``/``registers``).
    """

    def __init__(self, host: str, port: int = 502):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        from umodbus.client import tcp  # lazy import for testability

        self._tcp = tcp
        self.sock = socket.create_connection((self.host, self.port))
        return True

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        try:
            message = self._tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = self._tcp.send_message(message, self.sock)
        except Exception as exc:  # noqa: BLE001 - surface as a Modbus error result
            return _ModbusReadResult(error=str(exc))

        # umodbus returns a list of register values (ints) directly.
        if isinstance(response, (list, tuple)):
            return _ModbusReadResult(registers=list(response))
        return _ModbusReadResult(registers=[response])

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None


def _default_client(host: str, port: int = 502):
    client = _UModbusTcpClientAdapter(host, port=port)
    client.connect()
    return client


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self.client.read_holding_registers(address, count, unit=self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
