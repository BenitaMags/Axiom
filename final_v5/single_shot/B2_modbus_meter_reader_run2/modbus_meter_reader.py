# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (umodbus exposes plain functional helpers that

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket

from registers import REGISTERS, decode


class _UmodbusClientAdapter:
    """Adapts umodbus's socket + functional API to the read_holding_registers(
    address, count, unit=...) shape this module was originally written against
    for pymodbus, returning an object exposing `.isError()` and `.registers`.
    """

    class _Result:
        def __init__(self, registers=None, error=False):
            self.registers = registers or []
            self._error = error

        def isError(self):
            return self._error

    def __init__(self, host: str, port: int = 502):
        from umodbus.client import tcp

        self._tcp = tcp
        self.sock = socket.create_connection((host, port))

    def read_holding_registers(self, address, count, unit=1):
        try:
            message = self._tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = self._tcp.send_message(message, self.sock)
        except Exception:
            return self._Result(error=True)

        if isinstance(response, (list, tuple)):
            registers = list(response)
        else:
            registers = [response]
        return self._Result(registers=registers, error=False)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _default_client(host: str, port: int = 502):
    client = _UmodbusClientAdapter(host, port=port)
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
