# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


class _ReadResult:
    """Small adapter object mimicking the pymodbus response interface
    (``isError()`` / ``.registers``) on top of pyModbusTCP's plain
    list-or-None return value.
    """

    def __init__(self, registers):
        self.registers = registers if registers is not None else []

    def isError(self):
        return self.registers is None or len(self.registers) == 0 and self._failed

    _failed = False


class _ClientAdapter:
    """Wraps a pyModbusTCP ModbusClient to expose the
    ``read_holding_registers(address, count, unit=...)`` call shape used by
    :class:`MeterReader`, returning an object with ``isError()``/``registers``.
    """

    def __init__(self, host, port=502, unit_id=1):
        from pyModbusTCP.client import ModbusClient  # lazy import for testability

        self._client = ModbusClient(host=host, port=port, unit_id=unit_id, auto_open=True)

    def read_holding_registers(self, address, count, unit=1):
        self._client.unit_id = unit
        regs = self._client.read_holding_registers(address, count)
        result = _ReadResult(regs)
        result._failed = regs is None
        return result


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
