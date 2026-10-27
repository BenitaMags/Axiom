# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, auto_open=True)
    return client


class _ClientAdapter:
    """Adapts a pyModbusTCP ModbusClient to the read_holding_registers API
    expected by MeterReader (result object with .isError() and .registers)."""

    class _Result:
        def __init__(self, registers=None):
            self.registers = registers

        def isError(self):
            return self.registers is None

    def __init__(self, client):
        self._client = client

    def read_holding_registers(self, address, count, unit=1):
        try:
            self._client.unit_id = unit
        except AttributeError:
            pass
        regs = self._client.read_holding_registers(address, count)
        return self._Result(regs)


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        if client is not None:
            self.client = client
        else:
            self.client = _ClientAdapter(_default_client(host))
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
