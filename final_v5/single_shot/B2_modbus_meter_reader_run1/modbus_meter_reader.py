# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (umodbus returns a plain list of register

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket

from registers import REGISTERS, decode


class _UmodbusClientAdapter:
    """Adapts umodbus's functional TCP client API to the read_holding_registers(...)
    -> object-with-.isError()/.registers interface this module was written against.

    umodbus (https://pypi.org/project/umodbus/) exposes a low-level functional API:
    `umodbus.client.tcp.read_holding_registers(slave_id, starting_address, quantity)`
    builds an ADU which must be sent over a socket via
    `umodbus.client.tcp.send_message(adu, sock)`, which returns a plain list of
    decoded register values (or raises on error). This adapter wraps that so the
    rest of the code can keep using the pymodbus-shaped calling convention.
    """

    def __init__(self, host, port=502, timeout=5):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock = None

    def connect(self):
        self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        return True

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def read_holding_registers(self, address, count, unit=1):
        from umodbus.client import tcp
        from umodbus import exceptions as umodbus_exceptions

        class _Result:
            def __init__(self, registers=None, error=None):
                self.registers = registers
                self._error = error

            def isError(self):
                return self._error is not None

        try:
            adu = tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = tcp.send_message(adu, self._sock)
        except (umodbus_exceptions.ModbusError, OSError) as exc:
            return _Result(error=exc)

        # umodbus returns a plain list of ints for successful reads.
        return _Result(registers=list(response))


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
