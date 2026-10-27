# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _ReadResult:
    """Adapter object mimicking the minimal pymodbus response interface
    that this module relies on (`.isError()` and `.registers`).
    """

    def __init__(self, registers):
        self.registers = registers if registers is not None else []
        self._error = registers is None

    def isError(self) -> bool:
        return self._error


class _PyModbusTCPAdapter:
    """Adapts pyModbusTCP's ModbusClient to the read_holding_registers(...)
    signature used by MeterReader, including the `unit=` keyword argument.
    """

    def __init__(self, host: str, port: int = 502):
        from pyModbusTCP.client import ModbusClient  # lazy import for testability

        self._client = ModbusClient(host=host, port=port, auto_open=True)

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        # pyModbusTCP exposes unit id selection via the `unit_id` attribute.
        self._client.unit_id = unit
        registers = self._client.read_holding_registers(address, count)
        return _ReadResult(registers)


def _default_client(host: str, port: int = 502):
    client = _PyModbusTCPAdapter(host, port=port)
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
