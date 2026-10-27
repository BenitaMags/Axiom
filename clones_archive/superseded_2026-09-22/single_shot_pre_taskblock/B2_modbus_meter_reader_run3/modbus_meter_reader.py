# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _ClientAdapter:
    """Adapts pyModbusTCP's ModbusClient to expose a pymodbus-like read API.

    pyModbusTCP's `read_holding_registers` returns a plain list of register
    values on success or `None` on failure, whereas pymodbus returns a
    response object exposing `.isError()` and `.registers`. This adapter
    wraps the underlying pyModbusTCP client so callers can use a uniform,
    pymodbus-like interface.
    """

    class _Result:
        def __init__(self, registers):
            self.registers = registers

        def isError(self):
            return self.registers is None

    def __init__(self, host: str, port: int = 502):
        from pyModbusTCP.client import ModbusClient  # lazy import for testability

        self._client = ModbusClient(host=host, port=port, auto_open=True)

    def read_holding_registers(self, address, count, unit=1):
        self._client.unit_id = unit
        registers = self._client.read_holding_registers(address, count)
        return self._Result(registers)


def _default_client(host: str, port: int = 502):
    return _ClientAdapter(host, port=port)


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
