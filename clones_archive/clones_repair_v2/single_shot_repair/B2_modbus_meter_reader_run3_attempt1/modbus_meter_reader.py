# CHOSEN-TARGET: pyModbusTCP
"""modbus_meter_reader -- read a power meter's holding registers into a record.


read APIs and result objects differ (pymodbus returns a response object with

clone needs glue -- hence Category B. The client is dependency-injected for testing.
"""
from registers import REGISTERS, decode


def _default_client(host: str, port: int = 502):
    from pyModbusTCP.client import ModbusClient  # lazy import for testability

    client = ModbusClient(host=host, port=port, unit_id=1, auto_open=True)
    return client


class _ReadResult:
    """Adapter object exposing an `isError()`/`.registers` interface similar to
    what pymodbus's response objects provided, so decoding code stays the same."""

    def __init__(self, registers):
        self._registers = registers

    def isError(self):
        return self._registers is None

    @property
    def registers(self):
        return self._registers


class MeterReader:
    def __init__(self, host: str = None, client=None, unit: int = 1):
        self.client = client if client is not None else _default_client(host)
        self.unit = unit

    def _read_holding_registers(self, address, count, unit):
        """Call the underlying client's read API, normalizing to a
        `_ReadResult`-like object regardless of whether the injected client is
        a pyModbusTCP client (returns a list or None) or a test double already
        providing `.isError()`/`.registers`."""
        # Support pyModbusTCP-style clients: set unit id if supported, then read.
        set_unit = getattr(self.client, "unit_id", None)
        if hasattr(self.client, "unit_id"):
            try:
                self.client.unit_id = unit
            except Exception:
                pass

        raw = self.client.read_holding_registers(address, count)

        if isinstance(raw, list) or raw is None:
            return _ReadResult(raw)

        # Already an object exposing isError()/registers (e.g. test doubles).
        return raw

    def read_measurement(self) -> dict:
        """Read every configured register and return a decoded measurement record."""
        record = {}
        for name, (address, count, encoding, scale) in REGISTERS.items():
            result = self._read_holding_registers(address, count, self.unit)
            if result.isError():
                raise IOError(f"Modbus read failed for {name}")
            record[name] = decode(encoding, result.registers, scale)
        return record
