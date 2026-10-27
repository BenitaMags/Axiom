# CHOSEN-TARGET: umodbus
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _ReadResult:
    """Adapter result object mimicking pymodbus's response API.

    Exposes `.isError()` and `.registers` regardless of whether the
    underlying transport succeeded or raised an exception.
    """

    def __init__(self, registers=None, error=None):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error is not None


class _UModbusTcpClientAdapter:
    """Adapts umodbus's functional TCP client API to a pymodbus-like interface.

    umodbus exposes module-level functions (`umodbus.client.tcp`) that build
    ADU frames and a `send_message(adu, sock)` call that operates on a raw
    socket, rather than a stateful client object. This adapter hides that
    difference behind `.connect()` / `.read_holding_registers()`.
    """

    def __init__(self, host: str, port: int = 502):
        self.host = host
        self.port = port
        self._sock = None

    def connect(self):
        import socket

        self._sock = socket.create_connection((self.host, self.port))
        return True

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        from umodbus.client import tcp

        try:
            if self._sock is None:
                self.connect()
            message = tcp.read_holding_registers(
                slave_id=unit, starting_address=address, quantity=count
            )
            response = tcp.send_message(message, self._sock)
        except Exception as exc:  # noqa: BLE001 - translate any transport error
            return _ReadResult(error=exc)

        if isinstance(response, (list, tuple)):
            registers = list(response)
        else:
            registers = [response]
        return _ReadResult(registers=registers)


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
