# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pyModbusTCP returns a list of registers or

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class _ClientAdapter:
    """Wraps a pyModbusTCP ModbusClient to expose a pymodbus-like read API."""

    def __init__(self, client, unit: int = 1):
        self._client = client
        try:
            self._client.unit_id = unit
        except AttributeError:
            pass

    def read_holding_registers(self, address, count, unit=1):
        try:
            self._client.unit_id = unit
        except AttributeError:
            pass
        registers = self._client.read_holding_registers(address, count)
        return _ReadResult(registers)


class _ReadResult:
    def __init__(self, registers):
        self.registers = registers if registers is not None else []
        self._error = registers is None

    def isError(self):
        return self._error


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if client is not None:
            self.client = client
        else:
            raw_client = _default_client(host)
            self.client = _ClientAdapter(raw_client, unit)
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
