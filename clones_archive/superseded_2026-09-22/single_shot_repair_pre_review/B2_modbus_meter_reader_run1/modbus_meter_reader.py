# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _Result:
    """Adapts pyModbusTCP's plain list/None return into the response-object
    shape (.isError() / .registers) that this module's API relies on, matching
    the original pymodbus response contract used by callers and tests."""

    def __init__(self, registers):
        self._registers = registers

    def isError(self) -> bool:
        return self._registers is None

    @property
    def registers(self):
        return self._registers if self._registers is not None else []


class _ClientAdapter:
    """Wraps a pyModbusTCP ModbusClient to expose the pymodbus-style
    read_holding_registers(address, count, unit=...) -> response API."""

    def __init__(self, host, port=502):
        from pyModbusTCP.client import ModbusClient  # lazy import for testability

        self._client = ModbusClient(host=host, port=port, auto_open=True)

    def read_holding_registers(self, address, count, unit=1):
        if hasattr(self._client, "unit_id"):
            try:
                self._client.unit_id = unit
            except Exception:
                pass
        registers = self._client.read_holding_registers(address, count)
        return _Result(registers)


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
