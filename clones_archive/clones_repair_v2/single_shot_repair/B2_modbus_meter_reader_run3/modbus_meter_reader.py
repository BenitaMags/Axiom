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
        providing `.isError()`/`.registers`.

        The `unit` value is always passed through to the client call (as the
        original pymodbus-based code did with `unit=self.unit`), so that
        clients or test doubles which key off of it observe the correct value.
        """
        # pyModbusTCP clients don't accept a `unit` kwarg on the read call
        # itself -- they use a `unit_id` attribute instead. Keep that in sync
        # for real transports, but still pass `unit` through to the call so
        # that dependency-injected clients (including test doubles) that
        # accept it positionally/as a keyword continue to see it threaded
        # through, matching the original behaviour.
        if hasattr(self.client, "unit_id"):
            try:
                self.client.unit_id = unit
            except Exception:
                pass

        try:
            raw = self.client.read_holding_registers(address, count, unit)
        except TypeError:
            # Client doesn't accept a third positional argument (e.g. a real
            # pyModbusTCP client, whose signature is (reg_addr, reg_nb)).
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
