# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _UModbusClientAdapter:
    """Adapts umodbus's socket-based TCP client to the read_holding_registers(
    address, count, unit=...) -> response-object-with-.isError()/.registers
    interface expected by MeterReader, so the rest of the code (and any tests
    injecting a fake client with that interface) doesn't need to change.
    """

    class _Result:
        def __init__(self, registers=None, error=False):
            self.registers = registers or []
            self._error = error

        def isError(self):
            return self._error

    def __init__(self, host: str, port: int = 502):
        import socket
        from umodbus.client import tcp

        self._tcp = tcp
        self._sock = socket.create_connection((host, port))

    def connect(self):
        # Connection is already established in __init__; kept for API parity.
        return True

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass

    def read_holding_registers(self, address, count, unit=1):
        try:
            message = self._tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            registers = self._tcp.send_message(message, self._sock)
            return self._Result(registers=list(registers), error=False)
        except Exception:
            return self._Result(registers=[], error=True)


def _default_client(host: str, port: int = 502):
    client = _UModbusClientAdapter(host, port=port)
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
