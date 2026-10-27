# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pyModbusTCP returns a plain list of registers or
None on error, unlike the response-object based APIs of other Modbus libraries), so a
small adapter/result wrapper is used to keep the rest of this module unchanged -- hence
Category A (library-backed, with glue). The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _ReadResult:
    """Adapter object mimicking the response-object style used by this module.

    Wraps the plain list (or None) returned by pyModbusTCP's
    read_holding_registers() so callers can use .isError() / .registers as before.
    """

    def __init__(self, registers):
        self.registers = registers

    def isError(self) -> bool:
        return self.registers is None


class _PyModbusTCPAdapter:
    """Adapts pyModbusTCP.client.ModbusClient to the interface used by MeterReader."""

    def __init__(self, host: str, port: int = 502, unit_id: int = 1):
        from pyModbusTCP.client import ModbusClient  # lazy import for testability

        self._client = ModbusClient(host=host, port=port, unit_id=unit_id, auto_open=True)

    def read_holding_registers(self, address: int, count: int, unit: int = 1):
        self._client.unit_id = unit
        registers = self._client.read_holding_registers(address, count)
        return _ReadResult(registers)


def _default_client(host: str, port: int = 502):
    return _PyModbusTCPAdapter(host, port=port)


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
