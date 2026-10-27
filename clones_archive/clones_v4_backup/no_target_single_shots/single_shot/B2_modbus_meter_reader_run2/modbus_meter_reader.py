# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket

from registers import REGISTERS, decode


class _ErrorAwareResult:
    """Adapter object mimicking the pymodbus response interface.

    umodbus's client-side helpers return plain lists of register values (or
    raise exceptions on error), so we wrap the outcome in a small object that
    exposes the same `.isError()` / `.registers` surface the rest of this
    module (and its test suite) expects.
    """

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _UmodbusTcpClientAdapter:
    """Thin adapter exposing a pymodbus-like API on top of umodbus.

    umodbus's `umodbus.client.tcp` module is functional: you build a request
    ADU (`read_holding_registers`) and send it over a raw socket with
    `send_message`. This adapter hides that behind the same
    `read_holding_registers(address, count, unit=...)` method used elsewhere
    in this codebase.
    """

    def __init__(self, host: str, port: int = 502):
        self.host = host
        self.port = port
        self._sock = None

    def connect(self):
        from umodbus.client import tcp  # lazy import for testability

        self._tcp = tcp
        self._sock = socket.create_connection((self.host, self.port))
        return True

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        try:
            message = self._tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = self._tcp.send_message(message, self._sock)
        except Exception:
            return _ErrorAwareResult(error=True)

        if not isinstance(response, (list, tuple)):
            response = [response]
        return _ErrorAwareResult(registers=list(response), error=False)

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def _default_client(host: str, port: int = 502):
    client = _UmodbusTcpClientAdapter(host, port=port)
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
