# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (umodbus returns a plain list of register

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket

from registers import REGISTERS, decode


class _UmodbusClientAdapter:
    """Adapts umodbus's functional, socket-based TCP API to the
    ``read_holding_registers(address, count, unit=...)`` -> result-with-``.registers``
    shape that this module's callers expect (mirroring the old pymodbus surface).
    """

    def __init__(self, host: str, port: int = 502, timeout: float = 5.0):
        from umodbus.client import tcp  # lazy import for testability
        from umodbus.exceptions import ModbusError

        self._tcp = tcp
        self._ModbusError = ModbusError
        self._sock = socket.create_connection((host, port), timeout=timeout)

    def connect(self):
        # Connection is already established in __init__; kept for API parity.
        return True

    def read_holding_registers(self, address, count, unit=1):
        request_adu = self._tcp.read_holding_registers(
            slave_id=unit, starting_address=address, quantity=count
        )
        try:
            response = self._tcp.send_message(request_adu, self._sock)
        except self._ModbusError as exc:
            # Modbus-level exception response (e.g. illegal address) -> error result,
            # matching pymodbus's isError() semantics.
            return _ErrorResult(str(exc))
        # Connection-level failures (socket errors, malformed ADUs, etc.) are allowed
        # to propagate as exceptions, matching the original pymodbus client's
        # behaviour of raising rather than returning a normal-looking result.
        return _OkResult(response)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


class _OkResult:
    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class _ErrorResult:
    def __init__(self, message):
        self.message = message
        self.registers = []

    def isError(self):
        return True


def _default_client(host: str, port: int = 502):
    client = _UmodbusClientAdapter(host, port=port)
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
