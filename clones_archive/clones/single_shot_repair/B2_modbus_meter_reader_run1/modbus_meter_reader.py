# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pyModbusTCP returns a list of registers or
None on error, unlike pymodbus's response object with .isError()/.registers) --
glue code below adapts pyModbusTCP's client to the response-object style expected
by MeterReader. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _ReadResult:
    """Adapter object mimicking pymodbus's read_holding_registers() response.

    Exposes .isError() and .registers like the original pymodbus response object,
    so that MeterReader's logic (and any test doubles built against that shape)
    keeps working unchanged.
    """

    def __init__(self, registers):
        self.registers = registers if registers is not None else []
        self._error = registers is None

    def isError(self) -> bool:
        return self._error


class _PyModbusTCPClientAdapter:
    """Wraps a pyModbusTCP ModbusClient to present the pymodbus-style API
    used by MeterReader (read_holding_registers(address, count, unit=...)).
    """

    def __init__(self, host: str, port: int = 502, unit: int = 1):
        from pyModbusTCP.client import ModbusClient  # lazy import for testability

        self._client = ModbusClient(host=host, port=port, unit_id=unit, auto_open=True)

    def read_holding_registers(self, address, count, unit=None):
        if unit is not None:
            self._client.unit_id(unit)
        registers = self._client.read_holding_registers(address, count)
        return _ReadResult(registers)


def _default_client(host: str, port: int = 502, unit: int = 1):
    return _PyModbusTCPClientAdapter(host, port=port, unit=unit)


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host, unit=unit)
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
